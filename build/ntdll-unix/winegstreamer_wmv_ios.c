/* The WMV decoder transform of winegstreamer's unix side, on libavcodec.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * #included by winegstreamer_unixlib_ios.c (2026-10-22), after the WMA transform.
 *
 * WHY THIS EXISTS
 * ---------------
 * Resident Evil 2's cutscenes are Windows Media Video in an ASF file, read through a Media
 * Foundation source reader.  Wine's ASF media source (mfasfsrcsnk.dll on winedmo.dll, both opt-in
 * DLLs here) hands out COMPRESSED WMV samples, and the reader finds the decoder in the registry:
 * CLSID_CWMVDecMediaObject, wmvdecod.dll, which forwards to dlls/winegstreamer/video_decoder.c
 * (wmv_decoder_create) -- an IMFTransform/IMediaObject over wg_transform.  Upstream's wg_transform is a
 * GStreamer pipeline; until now this port refused every transform but the WMA family.  This file
 * is the video half: wmv1, wmv2, wmv3 and vc1 (WVC1, WMVA) on libavcodec
 * (build/ffmpeg/build.sh), converted in C to the formats video_decoder.c lists for the WMV
 * decoder: NV12, YV12, IYUV, I420, YUY2, UYVY, YVYU, RGB32, RGB24, RGB565 and RGB555.  (NV11 and
 * RGB8 are refused at create.)
 *
 * KEYED: nothing a game has today reaches this code.
 *   - Only the 64-bit table's create entry looks at a video input (wma_transform_create); the
 *     wow64 table (32-bit callers) is as it was.
 *   - The 64-bit table is bound to winegstreamer only for a game whose file has
 *     env.MADEIRA_WG_64BIT = 1 (virtual_ios.c); every other 64-bit process has the generic stub
 *     table, which answers every unix call with STATUS_NOT_SUPPORTED.
 *   - A video stream that is not WMV is refused exactly as before (STATUS_NOT_SUPPORTED, the
 *     handle left 0).
 *
 * THE CONTRACT WITH THE PE SIDE (dlls/winegstreamer/video_decoder.c, wg_sample.c, main.c)
 * --------------------------------------------------------------------------------------
 *   create: both types are struct wg_media_type holding an MFVIDEOFORMAT.  The codec's private
 *     data (the sequence header; MF_MT_USER_DATA) is the tail of the input MFVIDEOFORMAT after
 *     sizeof(MFVIDEOFORMAT).  wmv_decoder_create first calls check_video_transform_support with a
 *     WMV3 1920x1080 input that has NO user data; libavcodec cannot open a WMV3/VC-1 decoder
 *     without it, so a create without private data opens nothing and the decoder opens with
 *     the first sample, when the real type has been set.
 *   push_data: one compressed frame.  The MFT retries after MF_E_NOTACCEPTING once the output has
 *     been read.
 *   read_data: one picture, converted to the output type's format.  wmv_decoder_create sets
 *     preserve_timestamps, so the PE side takes the picture's time from the sample (pts, and
 *     WG_SAMPLE_FLAG_PRESERVE_TIMESTAMPS); without it every picture would get the first sample's
 *     time.  MF_E_TRANSFORM_NEED_MORE_INPUT when no picture is ready.
 *   Layout: the format's default stride (MFGetStrideForBitmapInfoHeader: YUV planar at the
 *     width, packed YUV at twice the width, RGB at the width times the bytes per pixel up to a
 *     multiple of 4), or the pitch of the sample's 2D buffer when it has one (struct
 *     wg_sample.stride).  An output type with MFVideoFlag_BottomUpLinearRep writes the rows
 *     bottom-up, as a Windows DIB.  A picture of another size than the type's is cropped, or padded
 *     with black.
 *
 * Log lines are "[wmv] ...", always printed (this unix side has no debug channel and the app runs
 * with WINEDEBUG=err+all); the repeating ones are capped.
 */

#include <math.h>
#include <time.h>
#include <libavutil/pixdesc.h>

#define MADEIRA_WMV_MAGIC 0x574d5644  /* 'WMVD' */
#define WMV_MAX_LOGS 120
#define WMV_MAX_FAIL_LOGS 8
#define WMV_MAX_DIMENSION 8192

static const GUID madeira_MFMediaType_Video =
    { 0x73646976, 0x0000, 0x0010, { 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71 } };

static unsigned int wmv_log_count;

static void wmv_log( const char *fmt, ... ) __attribute__((format(printf, 1, 2)));
static void wmv_log( const char *fmt, ... )
{
    char buf[640];
    va_list args;
    int n;

    va_start( args, fmt );
    n = vsnprintf( buf, sizeof(buf), fmt, args );
    va_end( args );
    if (n <= 0) return;
    dprintf( 2, "[wmv] %s%s", buf, buf[strlen(buf) - 1] == '\n' ? "" : "\n" );
}

#define WMV_LOG_CAPPED( ... )                                                              \
    do {                                                                                    \
        unsigned int n__ = __atomic_add_fetch( &wmv_log_count, 1, __ATOMIC_RELAXED );       \
        if (n__ <= WMV_MAX_LOGS) wmv_log( __VA_ARGS__ );                                    \
        else if (n__ == WMV_MAX_LOGS + 1) wmv_log( "further repeating lines suppressed\n" ); \
    } while (0)

enum wmv_fmt
{
    WMV_FMT_NONE = 0,
    WMV_FMT_NV12,
    WMV_FMT_YV12,
    WMV_FMT_I420,       /* I420 and IYUV: the same layout */
    WMV_FMT_YUY2,
    WMV_FMT_UYVY,
    WMV_FMT_YVYU,
    WMV_FMT_RGB32,      /* B, G, R, X (X = 0xff) */
    WMV_FMT_RGB24,
    WMV_FMT_RGB565,
    WMV_FMT_RGB555,
};

static const char *wmv_fmt_name( enum wmv_fmt fmt )
{
    static const char *const names[] = { "none", "NV12", "YV12", "I420", "YUY2", "UYVY", "YVYU", "RGB32", "RGB24",
                                         "RGB565", "RGB555" };
    return (unsigned)fmt < sizeof(names) / sizeof(names[0]) ? names[fmt] : "?";
}

#define WMV_FOURCC( a, b, c, d ) ((UINT32)(a) | ((UINT32)(b) << 8) | ((UINT32)(c) << 16) | ((UINT32)(d) << 24))

/* The two GUID families a subtype comes in: {Data1-0000-0010-8000-00AA00389B71} (Media Foundation:
 * a FOURCC or a D3DFMT in Data1) and {Data1-524F-11CE-9F53-0020AF0BA770} (DirectShow's RGB). */
static BOOL wmv_guid_is_mf( const GUID *g )
{
    static const BYTE tail[8] = { 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71 };
    return g->Data2 == 0 && g->Data3 == 0x0010 && !memcmp( g->Data4, tail, 8 );
}

static BOOL wmv_guid_is_ds( const GUID *g )
{
    static const BYTE tail[8] = { 0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70 };
    return g->Data2 == 0x524f && g->Data3 == 0x11ce && !memcmp( g->Data4, tail, 8 );
}

static enum wmv_fmt wmv_fmt_from_guid( const GUID *g )
{
    if (wmv_guid_is_mf( g ))
    {
        switch (g->Data1)
        {
        case WMV_FOURCC( 'N', 'V', '1', '2' ): return WMV_FMT_NV12;
        case WMV_FOURCC( 'Y', 'V', '1', '2' ): return WMV_FMT_YV12;
        case WMV_FOURCC( 'I', 'Y', 'U', 'V' ):
        case WMV_FOURCC( 'I', '4', '2', '0' ): return WMV_FMT_I420;
        case WMV_FOURCC( 'Y', 'U', 'Y', '2' ): return WMV_FMT_YUY2;
        case WMV_FOURCC( 'U', 'Y', 'V', 'Y' ): return WMV_FMT_UYVY;
        case WMV_FOURCC( 'Y', 'V', 'Y', 'U' ): return WMV_FMT_YVYU;
        case 21: /* D3DFMT_A8R8G8B8, MFVideoFormat_ARGB32 */
        case 22: /* D3DFMT_X8R8G8B8, MFVideoFormat_RGB32 */ return WMV_FMT_RGB32;
        case 20: /* D3DFMT_R8G8B8, MFVideoFormat_RGB24 */ return WMV_FMT_RGB24;
        case 23: /* D3DFMT_R5G6B5 */ return WMV_FMT_RGB565;
        case 24: /* D3DFMT_X1R5G5B5 */ return WMV_FMT_RGB555;
        }
    }
    else if (wmv_guid_is_ds( g ))
    {
        switch (g->Data1)
        {
        case 0xe436eb7e: return WMV_FMT_RGB32;   /* MEDIASUBTYPE_RGB32 */
        case 0xe436eb7d: return WMV_FMT_RGB24;
        case 0xe436eb7b: return WMV_FMT_RGB565;
        case 0xe436eb7c: return WMV_FMT_RGB555;
        }
    }
    return WMV_FMT_NONE;
}

static enum AVCodecID wmv_codec_from_guid( const GUID *g )
{
    if (!wmv_guid_is_mf( g )) return AV_CODEC_ID_NONE;
    switch (g->Data1)
    {
    case WMV_FOURCC( 'W', 'M', 'V', '1' ): return AV_CODEC_ID_WMV1;
    case WMV_FOURCC( 'W', 'M', 'V', '2' ): return AV_CODEC_ID_WMV2;
    case WMV_FOURCC( 'W', 'M', 'V', '3' ): return AV_CODEC_ID_WMV3;
    case WMV_FOURCC( 'W', 'V', 'C', '1' ):
    case WMV_FOURCC( 'W', 'M', 'V', 'A' ): return AV_CODEC_ID_VC1;
    }
    return AV_CODEC_ID_NONE;
}

/***********************************************************************
 *           layout and conversion (no libavcodec in here: the host test calls them directly)
 */
struct wmv_layout
{
    uint32_t stride[3];         /* bytes per row of each plane (packed formats: [0]) */
    size_t offset[3];
    uint32_t rows[3];
    size_t size;
};

/* `pitch`: the 2D buffer's pitch in bytes for the first plane, 0 for the format's default stride. */
static size_t wmv_layout_for( enum wmv_fmt fmt, uint32_t w, uint32_t h, uint32_t pitch, struct wmv_layout *l )
{
    uint32_t ch = (h + 1) / 2;

    memset( l, 0, sizeof(*l) );
    switch (fmt)
    {
    case WMV_FMT_NV12:
        l->stride[0] = l->stride[1] = pitch ? pitch : (w + 1) & ~1u;
        l->offset[1] = (size_t)l->stride[0] * h;
        l->rows[0] = h;
        l->rows[1] = ch;
        l->size = l->offset[1] + (size_t)l->stride[1] * ch;
        break;
    case WMV_FMT_YV12:
    case WMV_FMT_I420:
        l->stride[0] = pitch ? pitch : (w + 1) & ~1u;
        l->stride[1] = l->stride[2] = l->stride[0] / 2;
        l->offset[1] = (size_t)l->stride[0] * h;
        l->offset[2] = l->offset[1] + (size_t)l->stride[1] * ch;
        l->rows[0] = h;
        l->rows[1] = l->rows[2] = ch;
        l->size = l->offset[2] + (size_t)l->stride[2] * ch;
        break;
    case WMV_FMT_YUY2:
    case WMV_FMT_UYVY:
    case WMV_FMT_YVYU:
        l->stride[0] = pitch ? pitch : ((w + 1) & ~1u) * 2;
        l->rows[0] = h;
        l->size = (size_t)l->stride[0] * h;
        break;
    case WMV_FMT_RGB32:
        l->stride[0] = pitch ? pitch : w * 4;
        l->rows[0] = h;
        l->size = (size_t)l->stride[0] * h;
        break;
    case WMV_FMT_RGB24:
        l->stride[0] = pitch ? pitch : (w * 3 + 3) & ~3u;
        l->rows[0] = h;
        l->size = (size_t)l->stride[0] * h;
        break;
    case WMV_FMT_RGB565:
    case WMV_FMT_RGB555:
        l->stride[0] = pitch ? pitch : (w * 2 + 3) & ~3u;
        l->rows[0] = h;
        l->size = (size_t)l->stride[0] * h;
        break;
    default:
        return 0;
    }
    return l->size;
}

static BOOL wmv_fmt_is_rgb( enum wmv_fmt fmt )
{
    return fmt >= WMV_FMT_RGB32;
}

/* The smallest pitch that holds a row of the format's first plane. */
static uint32_t wmv_min_pitch( enum wmv_fmt fmt, uint32_t w )
{
    struct wmv_layout l;
    wmv_layout_for( fmt, w, 1, 0, &l );
    return l.stride[0];
}

struct wmv_picture
{
    const uint8_t *p[3];        /* Y, Cb, Cr (4:2:0 planar) */
    int stride[3];
    uint32_t width, height;
    int full_range;             /* Y 0-255 instead of 16-235 */
    int bt709;                  /* BT.709 instead of BT.601 */
};

static inline uint8_t wmv_clip8( int v )
{
    return v < 0 ? 0 : v > 255 ? 255 : (uint8_t)v;
}

struct wmv_coefs
{
    int y, rv, gu, gv, bu, yoff;
};

static void wmv_coefs( int bt709, int full, struct wmv_coefs *c )
{
    double kr = bt709 ? 0.2126 : 0.299, kb = bt709 ? 0.0722 : 0.114, kg = 1.0 - kr - kb;
    double ys = full ? 1.0 : 255.0 / 219.0, cs = full ? 1.0 : 255.0 / 224.0;

    c->y = (int)lround( ys * 65536 );
    c->rv = (int)lround( 2 * (1 - kr) * cs * 65536 );
    c->bu = (int)lround( 2 * (1 - kb) * cs * 65536 );
    c->gu = (int)lround( 2 * (1 - kb) * kb / kg * cs * 65536 );
    c->gv = (int)lround( 2 * (1 - kr) * kr / kg * cs * 65536 );
    c->yoff = full ? 0 : 16;
}

/* Writes the picture into `dst` (l->size bytes) as format `fmt`, w x h, rows in reverse order when
 * `flip`.  A picture smaller than w x h is padded with black, a larger one is cropped. */
static void wmv_convert( const struct wmv_picture *src, uint8_t *dst, enum wmv_fmt fmt, const struct wmv_layout *l,
                         uint32_t w, uint32_t h, int flip )
{
    uint32_t cw = src->width < w ? src->width : w, ch = src->height < h ? src->height : h;
    uint32_t x, y;
    struct wmv_coefs c;

    if (cw < w || ch < h)
    {
        /* black: Y 16 / chroma 128 for YUV, 0 for RGB (opaque) */
        if (fmt == WMV_FMT_YUY2 || fmt == WMV_FMT_YVYU)
            for (y = 0; y < l->rows[0]; y++)
                for (x = 0; x + 4 <= l->stride[0]; x += 4) memcpy( dst + (size_t)y * l->stride[0] + x, "\x10\x80\x10\x80", 4 );
        else if (fmt == WMV_FMT_UYVY)
            for (y = 0; y < l->rows[0]; y++)
                for (x = 0; x + 4 <= l->stride[0]; x += 4) memcpy( dst + (size_t)y * l->stride[0] + x, "\x80\x10\x80\x10", 4 );
        else if (!wmv_fmt_is_rgb( fmt ))
        {
            memset( dst, 16, l->offset[1] );
            memset( dst + l->offset[1], 128, l->size - l->offset[1] );
        }
        else
        {
            memset( dst, 0, l->size );
            if (fmt == WMV_FMT_RGB32)
                for (y = 0; y < l->rows[0]; y++)
                    for (x = 3; x < l->stride[0]; x += 4) dst[(size_t)y * l->stride[0] + x] = 0xff;
        }
    }

#define ROW( plane, row, rows ) (dst + l->offset[plane] + (size_t)l->stride[plane] * (flip ? (rows) - 1 - (row) : (row)))
    switch (fmt)
    {
    case WMV_FMT_NV12:
        for (y = 0; y < ch; y++) memcpy( ROW( 0, y, h ), src->p[0] + (size_t)y * src->stride[0], cw );
        for (y = 0; y < (ch + 1) / 2; y++)
        {
            const uint8_t *u = src->p[1] + (size_t)y * src->stride[1], *v = src->p[2] + (size_t)y * src->stride[2];
            uint8_t *o = ROW( 1, y, l->rows[1] );
            for (x = 0; x < (cw + 1) / 2; x++)
            {
                o[2 * x] = u[x];
                o[2 * x + 1] = v[x];
            }
        }
        break;
    case WMV_FMT_YV12:
    case WMV_FMT_I420:
    {
        int up = fmt == WMV_FMT_I420 ? 1 : 2, vp = 3 - up;
        for (y = 0; y < ch; y++) memcpy( ROW( 0, y, h ), src->p[0] + (size_t)y * src->stride[0], cw );
        for (y = 0; y < (ch + 1) / 2; y++)
        {
            memcpy( ROW( up, y, l->rows[up] ), src->p[1] + (size_t)y * src->stride[1], (cw + 1) / 2 );
            memcpy( ROW( vp, y, l->rows[vp] ), src->p[2] + (size_t)y * src->stride[2], (cw + 1) / 2 );
        }
        break;
    }
    case WMV_FMT_YUY2:
    case WMV_FMT_UYVY:
    case WMV_FMT_YVYU:
    {
        int yo = fmt == WMV_FMT_UYVY ? 1 : 0;           /* where Y0 sits in a 4-byte group */
        int uo = fmt == WMV_FMT_YUY2 ? 1 : fmt == WMV_FMT_UYVY ? 0 : 3;
        int vo = fmt == WMV_FMT_YUY2 ? 3 : fmt == WMV_FMT_UYVY ? 2 : 1;
        for (y = 0; y < ch; y++)
        {
            const uint8_t *yr = src->p[0] + (size_t)y * src->stride[0];
            const uint8_t *u = src->p[1] + (size_t)(y / 2) * src->stride[1], *v = src->p[2] + (size_t)(y / 2) * src->stride[2];
            uint8_t *o = ROW( 0, y, h );
            for (x = 0; x < cw; x += 2)
            {
                o[2 * x + yo] = yr[x];
                o[2 * x + yo + 2] = x + 1 < cw ? yr[x + 1] : yr[x];
                o[2 * x + uo] = u[x / 2];
                o[2 * x + vo] = v[x / 2];
            }
        }
        break;
    }
    case WMV_FMT_RGB32:
    case WMV_FMT_RGB24:
    case WMV_FMT_RGB565:
    case WMV_FMT_RGB555:
        wmv_coefs( src->bt709, src->full_range, &c );
        for (y = 0; y < ch; y++)
        {
            const uint8_t *yr = src->p[0] + (size_t)y * src->stride[0];
            const uint8_t *u = src->p[1] + (size_t)(y / 2) * src->stride[1], *v = src->p[2] + (size_t)(y / 2) * src->stride[2];
            uint8_t *o = ROW( 0, y, h );
            /* two pixels share their chroma: its three terms are worked out once per pair */
            for (x = 0; x < cw; x += 2)
            {
                int cb = u[x >> 1] - 128, cr = v[x >> 1] - 128;
                int rt = c.rv * cr, gt = c.gu * cb + c.gv * cr, bt = c.bu * cb;
                uint32_t n = x + 1 < cw ? 2 : 1, k;
                for (k = 0; k < n; k++)
                {
                    int yy = (yr[x + k] - c.yoff) * c.y + 32768;
                    uint8_t r = wmv_clip8( (yy + rt) >> 16 ), g = wmv_clip8( (yy - gt) >> 16 ), b = wmv_clip8( (yy + bt) >> 16 );
                    uint32_t px = x + k;
                    if (fmt == WMV_FMT_RGB32)
                    {
                        o[4 * px] = b;
                        o[4 * px + 1] = g;
                        o[4 * px + 2] = r;
                        o[4 * px + 3] = 0xff;
                    }
                    else if (fmt == WMV_FMT_RGB24)
                    {
                        o[3 * px] = b;
                        o[3 * px + 1] = g;
                        o[3 * px + 2] = r;
                    }
                    else
                    {
                        uint16_t v16 = fmt == WMV_FMT_RGB565 ? (uint16_t)(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
                                                             : (uint16_t)(((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3));
                        o[2 * px] = (uint8_t)v16;
                        o[2 * px + 1] = (uint8_t)(v16 >> 8);
                    }
                }
            }
        }
        break;
    default:
        break;
    }
#undef ROW
}

/***********************************************************************
 *           time stamps of a decoder that reorders
 *
 * A picture's time is its own packet's (libavcodec carries it through).  A stream with B frames
 * (avctx->has_b_frames > 0) is stored in decode order and comes out in display order, so the
 * packets' times come out out of order too (I0 P3 B1 B2 read 0 3 1 2 for the pictures 0 1 2 3).
 * Such a stream is stamped the way Media Foundation's own decoders do it: every picture takes
 * the oldest time still waiting, so the times leave in ascending order.  A stream without B
 * frames keeps every picture's own time.
 */
#define WMV_TS_MAX 64

struct wmv_ts
{
    INT64 t[WMV_TS_MAX];
    unsigned int n;
};

static void wmv_ts_insert( struct wmv_ts *ts, INT64 t )
{
    unsigned int i;

    if (ts->n == WMV_TS_MAX)
    {
        /* a stream that never gives pictures back: forget the oldest */
        memmove( ts->t, ts->t + 1, (WMV_TS_MAX - 1) * sizeof(ts->t[0]) );
        ts->n--;
    }
    for (i = ts->n; i > 0 && ts->t[i - 1] > t; i--) ts->t[i] = ts->t[i - 1];
    ts->t[i] = t;
    ts->n++;
}

static BOOL wmv_ts_take( struct wmv_ts *ts, INT64 *t )
{
    if (!ts->n) return FALSE;
    *t = ts->t[0];
    memmove( ts->t, ts->t + 1, (ts->n - 1) * sizeof(ts->t[0]) );
    ts->n--;
    return TRUE;
}

/***********************************************************************
 *           the transform
 */
struct wmv_transform
{
    UINT32 magic;               /* first member: wma_transform's too, the entry points tell them apart */
    pthread_mutex_t lock;

    enum AVCodecID codec_id;
    UINT32 fourcc;
    const AVCodec *codec;
    AVCodecContext *avctx;
    AVFrame *frame;             /* a picture decoded but not read yet (read_data had no room) */
    BOOL have_frame;
    AVPacket *packet;
    BYTE *extradata;
    UINT32 extradata_size;
    BOOL open_failed;

    UINT32 in_width, in_height;
    UINT32 fps_n, fps_d;
    BOOL preserve_timestamps;

    enum wmv_fmt out_fmt;
    UINT32 out_width, out_height;
    BOOL out_flip;
    BYTE *out_format;           /* the MFVIDEOFORMAT as set, handed back by get_output_type */
    UINT32 out_format_size;

    BOOL draining;
    BOOL eof;                   /* drained: nothing more will come out until a flush */
    BOOL discontinuity;
    BOOL accepts_input;
    struct wmv_ts pending_ts;   /* the times of the packets whose pictures are not out yet */
    BOOL first_frame_logged;
    INT64 last_pts;
    BOOL have_last_pts;

    /* what the log's summary line reports */
    UINT64 pushed, pushed_bytes, frames, no_room;
    UINT32 send_errors, recv_errors, convert_unsupported;
    double decode_ms, decode_ms_max, convert_ms, convert_ms_max;
};

static struct wmv_transform *wmv_get( wg_transform_t handle )
{
    struct wmv_transform *transform = (struct wmv_transform *)(UINT_PTR)handle;

    if (!transform || transform->magic != MADEIRA_WMV_MAGIC) return NULL;
    return transform;
}

static BOOL wmv_is_handle( wg_transform_t handle )
{
    return wmv_get( handle ) != NULL;
}

static double wmv_now_ms( void )
{
    struct timespec ts;
    clock_gettime( CLOCK_MONOTONIC, &ts );
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

static const MFVIDEOFORMAT *wmv_video_format( const struct wg_media_type *type )
{
    const MFVIDEOFORMAT *format;

    if (!IsEqualGUID( &type->major, &madeira_MFMediaType_Video )) return NULL;
    if (!(format = type->u.video) || type->format_size < sizeof(*format)) return NULL;
    if (format->dwSize < sizeof(*format) || format->dwSize > type->format_size) return NULL;
    return format;
}

/* The output type's geometry: the frame size and the row order. */
static BOOL wmv_set_output( struct wmv_transform *transform, const struct wg_media_type *type )
{
    const MFVIDEOFORMAT *format = wmv_video_format( type );
    enum wmv_fmt fmt;
    BYTE *copy;
    UINT32 w, h;

    if (!format) return FALSE;
    if (!(fmt = wmv_fmt_from_guid( &format->guidFormat ))) return FALSE;
    w = format->videoInfo.dwWidth ? format->videoInfo.dwWidth : transform->in_width;
    h = format->videoInfo.dwHeight ? format->videoInfo.dwHeight : transform->in_height;
    if (!w || !h || w > WMV_MAX_DIMENSION || h > WMV_MAX_DIMENSION) return FALSE;
    if (!(copy = malloc( format->dwSize ))) return FALSE;
    memcpy( copy, format, format->dwSize );

    free( transform->out_format );
    transform->out_format = copy;
    transform->out_format_size = format->dwSize;
    transform->out_fmt = fmt;
    transform->out_width = w;
    transform->out_height = h;
    transform->out_flip = !!(format->videoInfo.VideoFlags & MFVideoFlag_BottomUpLinearRep);
    return TRUE;
}

static void wmv_free( struct wmv_transform *transform )
{
    if (transform->avctx) avcodec_free_context( &transform->avctx );
    if (transform->frame) av_frame_free( &transform->frame );
    if (transform->packet) av_packet_free( &transform->packet );
    free( transform->extradata );
    free( transform->out_format );
    pthread_mutex_destroy( &transform->lock );
    transform->magic = 0;
    free( transform );
}

/* Opens libavcodec's decoder.  Needs the private data for WMV3 and VC-1 (see the header comment);
 * WMV1 and WMV2 open without it. */
static BOOL wmv_open( struct wmv_transform *transform )
{
    char err[AV_ERROR_MAX_STRING_SIZE];
    int ret;

    if (transform->avctx) return TRUE;
    if (transform->open_failed) return FALSE;

    if (!(transform->avctx = avcodec_alloc_context3( transform->codec )))
    {
        transform->open_failed = TRUE;
        return FALSE;
    }
    transform->avctx->opaque = WMV_DECODER_MARKER;
    transform->avctx->width = transform->in_width;
    transform->avctx->height = transform->in_height;
    transform->avctx->codec_tag = transform->fourcc;
    transform->avctx->pkt_timebase = (AVRational){1, 10000000};
    transform->avctx->thread_count = 1;
    if (transform->extradata_size)
    {
        if (!(transform->avctx->extradata = av_mallocz( transform->extradata_size + AV_INPUT_BUFFER_PADDING_SIZE )))
        {
            avcodec_free_context( &transform->avctx );
            transform->open_failed = TRUE;
            return FALSE;
        }
        memcpy( transform->avctx->extradata, transform->extradata, transform->extradata_size );
        transform->avctx->extradata_size = transform->extradata_size;
    }

    if ((ret = avcodec_open2( transform->avctx, transform->codec, NULL )) < 0)
    {
        av_strerror( ret, err, sizeof(err) );
        wmv_log( "avcodec_open2(%s) failed: %s (%d), %ux%u extradata=%u bytes\n", transform->codec->name, err, ret,
                 (unsigned)transform->in_width, (unsigned)transform->in_height, (unsigned)transform->extradata_size );
        avcodec_free_context( &transform->avctx );
        transform->open_failed = TRUE;
        return FALSE;
    }

    wmv_log( "decoder opened: %s (%s), libavcodec %s, coded %dx%d, profile %d, extradata=%u bytes (transform %p)\n",
             transform->codec->name, transform->codec->long_name, av_version_info(), transform->avctx->coded_width,
             transform->avctx->coded_height, transform->avctx->profile, (unsigned)transform->extradata_size, transform );
    return TRUE;
}

static NTSTATUS wmv_transform_create( struct wg_transform_create_params *params )
{
    const MFVIDEOFORMAT *in = wmv_video_format( &params->input_type );
    const MFVIDEOFORMAT *out = wmv_video_format( &params->output_type );
    struct wmv_transform *transform;
    enum AVCodecID codec_id;

    params->transform = 0;

    if (!in || !out) return STATUS_NOT_SUPPORTED;
    if ((codec_id = wmv_codec_from_guid( &in->guidFormat )) == AV_CODEC_ID_NONE)
    {
        WMV_LOG_CAPPED( "video input %08x-... is not a WMV stream; refusing\n", (unsigned)in->guidFormat.Data1 );
        return STATUS_NOT_SUPPORTED;
    }
    if (!wmv_fmt_from_guid( &out->guidFormat ))
    {
        WMV_LOG_CAPPED( "output format %08x-... is not one this decoder converts to; refusing\n",
                        (unsigned)out->guidFormat.Data1 );
        return STATUS_NOT_SUPPORTED;
    }
    if (!(transform = calloc( 1, sizeof(*transform) ))) return STATUS_NO_MEMORY;
    pthread_mutex_init( &transform->lock, NULL );
    transform->magic = MADEIRA_WMV_MAGIC;
    transform->codec_id = codec_id;
    transform->fourcc = in->guidFormat.Data1;
    transform->in_width = in->videoInfo.dwWidth;
    transform->in_height = in->videoInfo.dwHeight;
    transform->fps_n = in->videoInfo.FramesPerSecond.Numerator;
    transform->fps_d = in->videoInfo.FramesPerSecond.Denominator;
    transform->preserve_timestamps = params->attrs.preserve_timestamps;
    transform->accepts_input = TRUE;

    if (!(transform->codec = avcodec_find_decoder( codec_id )))
    {
        wmv_log( "libavcodec has no decoder for codec id %d\n", codec_id );
        wmv_free( transform );
        return STATUS_NOT_SUPPORTED;
    }
    if (!(transform->frame = av_frame_alloc()) || !(transform->packet = av_packet_alloc()))
    {
        wmv_free( transform );
        return STATUS_NO_MEMORY;
    }
    if (!wmv_set_output( transform, &params->output_type ))
    {
        wmv_log( "refusing the output type (format %s, %ux%u)\n", wmv_fmt_name( wmv_fmt_from_guid( &out->guidFormat ) ),
                 (unsigned)out->videoInfo.dwWidth, (unsigned)out->videoInfo.dwHeight );
        wmv_free( transform );
        return STATUS_NOT_SUPPORTED;
    }

    if (in->dwSize > sizeof(*in))
    {
        transform->extradata_size = in->dwSize - sizeof(*in);
        if (!(transform->extradata = malloc( transform->extradata_size )))
        {
            wmv_free( transform );
            return STATUS_NO_MEMORY;
        }
        memcpy( transform->extradata, (const BYTE *)in + sizeof(*in), transform->extradata_size );
    }

    wmv_log( "decoder created fmt=%s tag=%.4s %ux%u fps=%u/%u extradata=%u -> %s %ux%u%s (transform %p)\n",
             transform->codec->name, (const char *)&transform->fourcc, (unsigned)transform->in_width,
             (unsigned)transform->in_height, (unsigned)transform->fps_n, (unsigned)transform->fps_d,
             (unsigned)transform->extradata_size, wmv_fmt_name( transform->out_fmt ), (unsigned)transform->out_width,
             (unsigned)transform->out_height, transform->out_flip ? " bottom-up" : "", transform );

    /* The private data is there: open now, so a stream libavcodec rejects fails here and not at
     * the first sample.  Without it (the support check) the decoder opens with the first sample. */
    if (transform->extradata_size && !wmv_open( transform ))
    {
        wmv_free( transform );
        return STATUS_NOT_SUPPORTED;
    }

    pthread_once( &wma_av_log_once, wma_install_av_log );
    params->transform = (wg_transform_t)(UINT_PTR)transform;
    return STATUS_SUCCESS;
}

static NTSTATUS wmv_destroy( wg_transform_t handle )
{
    struct wmv_transform *transform = wmv_get( handle );

    if (!transform) return STATUS_INVALID_HANDLE;
    pthread_mutex_lock( &transform->lock );
    if (transform->pushed || transform->frames)
        wmv_log( "stats (transform %p): pushed=%llu (%llu bytes) pictures=%llu no_room=%llu send_errors=%u "
                 "recv_errors=%u unconvertible=%u decode avg=%.2f max=%.2f ms, convert avg=%.2f max=%.2f ms\n",
                 transform, (unsigned long long)transform->pushed, (unsigned long long)transform->pushed_bytes,
                 (unsigned long long)transform->frames, (unsigned long long)transform->no_room, transform->send_errors,
                 transform->recv_errors, transform->convert_unsupported,
                 transform->frames ? transform->decode_ms / transform->frames : 0.0, transform->decode_ms_max,
                 transform->frames ? transform->convert_ms / transform->frames : 0.0, transform->convert_ms_max );
    pthread_mutex_unlock( &transform->lock );
    wmv_free( transform );
    return STATUS_SUCCESS;
}

static NTSTATUS wmv_push_data( wg_transform_t handle, struct wg_sample *sample, const void *data, HRESULT *result )
{
    struct wmv_transform *transform = wmv_get( handle );
    char err[AV_ERROR_MAX_STRING_SIZE];
    AVPacket *pkt;
    double t0;
    int ret;

    if (!transform || !sample) return STATUS_INVALID_HANDLE;
    if (sample->size && !data) return STATUS_INVALID_PARAMETER;

    pthread_mutex_lock( &transform->lock );

    if (!sample->size)
    {
        /* an empty packet would tell libavcodec the stream has ended */
        pthread_mutex_unlock( &transform->lock );
        *result = S_OK;
        return STATUS_SUCCESS;
    }
    if (!wmv_open( transform ))
    {
        pthread_mutex_unlock( &transform->lock );
        WMV_LOG_CAPPED( "push refused: the decoder is not open (avcodec_open2 failed, or the type has no private data)\n" );
        *result = E_FAIL;
        return STATUS_SUCCESS;
    }
    /* New input after a drain starts a new stream. */
    if (transform->draining)
    {
        if (transform->have_frame)
        {
            av_frame_unref( transform->frame );
            transform->have_frame = FALSE;
        }
        avcodec_flush_buffers( transform->avctx );
        transform->eof = transform->draining = FALSE;
        transform->pending_ts.n = 0;
    }

    pkt = transform->packet;
    if (av_new_packet( pkt, sample->size ) < 0)
    {
        pthread_mutex_unlock( &transform->lock );
        return STATUS_NO_MEMORY;
    }
    memcpy( pkt->data, data, sample->size );
    pkt->pts = (sample->flags & WG_SAMPLE_FLAG_HAS_PTS) ? (INT64)sample->pts : AV_NOPTS_VALUE;
    pkt->dts = pkt->pts;
    pkt->duration = (sample->flags & WG_SAMPLE_FLAG_HAS_DURATION) ? (INT64)sample->duration : 0;
    if (sample->flags & WG_SAMPLE_FLAG_SYNC_POINT) pkt->flags |= AV_PKT_FLAG_KEY;
    if (sample->flags & WG_SAMPLE_FLAG_DISCONTINUITY) transform->discontinuity = TRUE;

    t0 = wmv_now_ms();
    ret = avcodec_send_packet( transform->avctx, pkt );
    t0 = wmv_now_ms() - t0;
    av_packet_unref( pkt );

    if (ret == AVERROR(EAGAIN))
    {
        /* a picture is waiting to be read: the caller reads it and pushes again */
        transform->accepts_input = FALSE;
        transform->no_room++;
        pthread_mutex_unlock( &transform->lock );
        *result = MF_E_NOTACCEPTING;
        return STATUS_SUCCESS;
    }
    transform->accepts_input = TRUE;
    if (ret < 0)
    {
        /* The packet is lost and the stream goes on, as a GStreamer decoder would drop it. */
        if (transform->send_errors++ < WMV_MAX_FAIL_LOGS)
        {
            av_strerror( ret, err, sizeof(err) );
            wmv_log( "avcodec_send_packet failed: %s (%d), dropping %u bytes (pts %lld)\n", err, ret, (unsigned)sample->size,
                     (sample->flags & WG_SAMPLE_FLAG_HAS_PTS) ? (long long)sample->pts : -1LL );
        }
    }
    else
    {
        if (sample->flags & WG_SAMPLE_FLAG_HAS_PTS) wmv_ts_insert( &transform->pending_ts, (INT64)sample->pts );
        transform->pushed++;
        transform->pushed_bytes += sample->size;
        transform->decode_ms += t0;
        if (t0 > transform->decode_ms_max) transform->decode_ms_max = t0;
        if (transform->pushed == 1)
            wmv_log( "first sample accepted: %u bytes, pts=%lld, key=%d (transform %p)\n", (unsigned)sample->size,
                     (sample->flags & WG_SAMPLE_FLAG_HAS_PTS) ? (long long)sample->pts : -1LL,
                     !!(sample->flags & WG_SAMPLE_FLAG_SYNC_POINT), transform );
    }
    pthread_mutex_unlock( &transform->lock );
    *result = S_OK;
    return STATUS_SUCCESS;
}

/* The picture's colour parameters: what the stream says, else BT.601 below 720 lines and BT.709
 * above, as players do. */
static void wmv_picture_from_frame( const AVFrame *frame, struct wmv_picture *pic )
{
    memset( pic, 0, sizeof(*pic) );
    pic->p[0] = frame->data[0];
    pic->p[1] = frame->data[1];
    pic->p[2] = frame->data[2];
    pic->stride[0] = frame->linesize[0];
    pic->stride[1] = frame->linesize[1];
    pic->stride[2] = frame->linesize[2];
    pic->width = frame->width;
    pic->height = frame->height;
    pic->full_range = frame->color_range == AVCOL_RANGE_JPEG || frame->format == AV_PIX_FMT_YUVJ420P;
    if (frame->colorspace == AVCOL_SPC_BT709) pic->bt709 = 1;
    else if (frame->colorspace == AVCOL_SPC_UNSPECIFIED || frame->colorspace == AVCOL_SPC_RESERVED)
        pic->bt709 = frame->height >= 720;
}

static NTSTATUS wmv_read_data( wg_transform_t handle, struct wg_sample *sample, void *data, HRESULT *result )
{
    struct wmv_transform *transform = wmv_get( handle );
    char err[AV_ERROR_MAX_STRING_SIZE];
    struct wmv_layout layout;
    struct wmv_picture pic;
    AVFrame *frame;
    INT64 pts;
    UINT32 pitch = 0;
    double t0, t1;
    int ret;

    if (!transform || !sample) return STATUS_INVALID_HANDLE;
    if (sample->max_size && !data) return STATUS_INVALID_PARAMETER;

    pthread_mutex_lock( &transform->lock );
    frame = transform->frame;
    sample->size = 0;

    if (!transform->have_frame)
    {
        if (!transform->avctx)
        {
            pthread_mutex_unlock( &transform->lock );
            *result = MF_E_TRANSFORM_NEED_MORE_INPUT;
            return STATUS_SUCCESS;
        }
        t0 = wmv_now_ms();
        ret = avcodec_receive_frame( transform->avctx, frame );
        t0 = wmv_now_ms() - t0;
        if (ret == AVERROR(EAGAIN) || ret == AVERROR_EOF)
        {
            if (ret == AVERROR_EOF) transform->eof = TRUE;
            transform->accepts_input = TRUE;
            pthread_mutex_unlock( &transform->lock );
            *result = MF_E_TRANSFORM_NEED_MORE_INPUT;
            return STATUS_SUCCESS;
        }
        if (ret < 0)
        {
            if (transform->recv_errors++ < WMV_MAX_FAIL_LOGS)
            {
                av_strerror( ret, err, sizeof(err) );
                wmv_log( "avcodec_receive_frame failed: %s (%d)\n", err, ret );
            }
            pthread_mutex_unlock( &transform->lock );
            *result = MF_E_TRANSFORM_NEED_MORE_INPUT;
            return STATUS_SUCCESS;
        }
        transform->decode_ms += t0;
        if (t0 > transform->decode_ms_max) transform->decode_ms_max = t0;
        transform->have_frame = TRUE;
    }

    if (frame->format != AV_PIX_FMT_YUV420P && frame->format != AV_PIX_FMT_YUVJ420P)
    {
        /* every WMV/VC-1 decoder in libavcodec produces 4:2:0 planar; anything else is a bug here */
        if (!transform->convert_unsupported++)
            wmv_log( "unexpected picture format %d (%s); the picture is dropped\n", frame->format,
                     av_get_pix_fmt_name( frame->format ) ? av_get_pix_fmt_name( frame->format ) : "?" );
        av_frame_unref( frame );
        transform->have_frame = FALSE;
        pthread_mutex_unlock( &transform->lock );
        *result = MF_E_TRANSFORM_NEED_MORE_INPUT;
        return STATUS_SUCCESS;
    }

    /* A 2D buffer's pitch (struct wg_sample.stride) is the first plane's row length. */
    if (sample->stride && sample->stride >= wmv_min_pitch( transform->out_fmt, transform->out_width ))
        pitch = sample->stride;
    wmv_layout_for( transform->out_fmt, transform->out_width, transform->out_height, pitch, &layout );
    if (sample->max_size < layout.size)
    {
        /* Keep the picture: the caller comes back with a larger buffer. */
        sample->size = layout.size;
        pthread_mutex_unlock( &transform->lock );
        WMV_LOG_CAPPED( "output buffer too small: %u bytes for %zu (%s %ux%u, pitch %u)\n", (unsigned)sample->max_size,
                        layout.size, wmv_fmt_name( transform->out_fmt ), (unsigned)transform->out_width,
                        (unsigned)transform->out_height, (unsigned)pitch );
        *result = MF_E_BUFFERTOOSMALL;
        return STATUS_SUCCESS;
    }

    if (!transform->first_frame_logged)
    {
        transform->first_frame_logged = TRUE;
        wmv_log( "first picture: %dx%d %s, key=%d, pts=%lld, colorspace=%d range=%d -> %s %ux%u (transform %p)\n", frame->width,
                 frame->height, av_get_pix_fmt_name( frame->format ), !!(frame->flags & AV_FRAME_FLAG_KEY),
                 (long long)frame->pts, frame->colorspace, frame->color_range, wmv_fmt_name( transform->out_fmt ),
                 (unsigned)transform->out_width, (unsigned)transform->out_height, transform );
        if (frame->width != (int)transform->out_width || frame->height != (int)transform->out_height)
            wmv_log( "the picture is %dx%d and the output type %ux%u: cropped or padded with black\n", frame->width,
                     frame->height, (unsigned)transform->out_width, (unsigned)transform->out_height );
    }

    t1 = wmv_now_ms();
    wmv_picture_from_frame( frame, &pic );
    wmv_convert( &pic, data, transform->out_fmt, &layout, transform->out_width, transform->out_height, transform->out_flip );
    t1 = wmv_now_ms() - t1;
    transform->convert_ms += t1;
    if (t1 > transform->convert_ms_max) transform->convert_ms_max = t1;

    sample->size = layout.size;
    sample->flags &= ~(WG_SAMPLE_FLAG_INCOMPLETE | WG_SAMPLE_FLAG_HAS_PTS | WG_SAMPLE_FLAG_HAS_DURATION |
                       WG_SAMPLE_FLAG_SYNC_POINT | WG_SAMPLE_FLAG_DISCONTINUITY | WG_SAMPLE_FLAG_PRESERVE_TIMESTAMPS);
    if (frame->flags & AV_FRAME_FLAG_KEY) sample->flags |= WG_SAMPLE_FLAG_SYNC_POINT;
    pts = frame->pts != AV_NOPTS_VALUE ? frame->pts : frame->best_effort_timestamp;
    if (transform->avctx->has_b_frames > 0)
    {
        INT64 oldest;
        if (wmv_ts_take( &transform->pending_ts, &oldest )) pts = oldest;
    }
    else
        transform->pending_ts.n = 0;   /* every picture has its own time: nothing to hold */
    if (pts != AV_NOPTS_VALUE)
    {
        sample->flags |= WG_SAMPLE_FLAG_HAS_PTS;
        sample->pts = pts;
        transform->last_pts = pts;
        transform->have_last_pts = TRUE;
    }
    if (frame->duration > 0)
    {
        sample->flags |= WG_SAMPLE_FLAG_HAS_DURATION;
        sample->duration = frame->duration;
    }
    else if (transform->fps_n && transform->fps_d)
    {
        sample->flags |= WG_SAMPLE_FLAG_HAS_DURATION;
        sample->duration = (UINT64)10000000 * transform->fps_d / transform->fps_n;
    }
    if (transform->preserve_timestamps) sample->flags |= WG_SAMPLE_FLAG_PRESERVE_TIMESTAMPS;
    if (transform->discontinuity)
    {
        sample->flags |= WG_SAMPLE_FLAG_DISCONTINUITY;
        transform->discontinuity = FALSE;
    }

    transform->frames++;
    av_frame_unref( frame );
    transform->have_frame = FALSE;
    transform->accepts_input = TRUE;
    pthread_mutex_unlock( &transform->lock );
    *result = S_OK;
    return STATUS_SUCCESS;
}

static NTSTATUS wmv_get_output_type( wg_transform_t handle, struct wg_media_type *type )
{
    struct wmv_transform *transform = wmv_get( handle );
    UINT32 size;

    if (!transform) return STATUS_INVALID_HANDLE;
    pthread_mutex_lock( &transform->lock );
    size = transform->out_format_size;
    type->major = madeira_MFMediaType_Video;
    /* Two-phase, as main.c's wg_transform_get_output_type() expects. */
    if (!type->u.format || type->format_size < size)
    {
        type->format_size = size;
        pthread_mutex_unlock( &transform->lock );
        return STATUS_BUFFER_TOO_SMALL;
    }
    memcpy( type->u.format, transform->out_format, size );
    type->format_size = size;
    pthread_mutex_unlock( &transform->lock );
    return STATUS_SUCCESS;
}

static NTSTATUS wmv_set_output_type( wg_transform_t handle, const struct wg_media_type *type )
{
    struct wmv_transform *transform = wmv_get( handle );
    BOOL ok;

    if (!transform) return STATUS_INVALID_HANDLE;
    pthread_mutex_lock( &transform->lock );
    /* A picture already decoded stays valid: it is converted when it is read. */
    ok = wmv_set_output( transform, type );
    if (ok)
        WMV_LOG_CAPPED( "output type set to %s %ux%u%s (transform %p)\n", wmv_fmt_name( transform->out_fmt ),
                        (unsigned)transform->out_width, (unsigned)transform->out_height,
                        transform->out_flip ? " bottom-up" : "", transform );
    pthread_mutex_unlock( &transform->lock );
    return ok ? STATUS_SUCCESS : STATUS_NOT_SUPPORTED;
}

static NTSTATUS wmv_get_status( wg_transform_t handle, UINT32 *accepts_input )
{
    struct wmv_transform *transform = wmv_get( handle );

    if (!transform) return STATUS_INVALID_HANDLE;
    pthread_mutex_lock( &transform->lock );
    *accepts_input = transform->accepts_input;
    pthread_mutex_unlock( &transform->lock );
    return STATUS_SUCCESS;
}

/* Drain: the decoder is told no more input comes and the pictures it still holds are read as
 * usual; MF_E_TRANSFORM_NEED_MORE_INPUT ends them. */
static NTSTATUS wmv_drain( wg_transform_t handle )
{
    struct wmv_transform *transform = wmv_get( handle );
    int ret;

    if (!transform) return STATUS_INVALID_HANDLE;
    pthread_mutex_lock( &transform->lock );
    if (transform->avctx && !transform->draining)
    {
        ret = avcodec_send_packet( transform->avctx, NULL );
        transform->draining = TRUE;
        WMV_LOG_CAPPED( "drain: transform=%p after %llu samples (%d)\n", transform, (unsigned long long)transform->pushed, ret );
    }
    pthread_mutex_unlock( &transform->lock );
    return STATUS_SUCCESS;
}

static NTSTATUS wmv_flush( wg_transform_t handle )
{
    struct wmv_transform *transform = wmv_get( handle );

    if (!transform) return STATUS_INVALID_HANDLE;
    pthread_mutex_lock( &transform->lock );
    if (transform->have_frame)
    {
        av_frame_unref( transform->frame );
        transform->have_frame = FALSE;
    }
    if (transform->avctx) avcodec_flush_buffers( transform->avctx );
    transform->draining = transform->eof = FALSE;
    transform->pending_ts.n = 0;
    transform->discontinuity = TRUE;
    transform->accepts_input = TRUE;
    WMV_LOG_CAPPED( "flush: transform=%p after %llu samples, %llu pictures\n", transform, (unsigned long long)transform->pushed,
                    (unsigned long long)transform->frames );
    pthread_mutex_unlock( &transform->lock );
    return STATUS_SUCCESS;
}
