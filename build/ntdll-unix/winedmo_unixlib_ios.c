/* iOS unix side for dlls/winedmo: the FFmpeg demuxer behind Wine's ASF media source.
 *
 * SPDX-License-Identifier: LGPL-2.1-or-later
 * Copyright 2024 Rémi Bernon for CodeWeavers   (dlls/winedmo, which this file is taken from)
 *
 * Modified by BCD, 2026-10-22: one file instead of dlls/winedmo/{unixlib.c, unix_demuxer.c,
 * unix_media_type.c}; no Wine debug channel (the lines are dprintf(2, "[winedmo] ...") and always
 * printed, capped); no bitstream filter layer (libavcodec's bsf set is not built here and H.264 in
 * ASF is not supported); the demuxer is locked; the failure path of demuxer_create no longer reads
 * a freed context; an AVIO buffer is allocated.  The table is bound by name in
 * build/ntdll-unix/virtual_ios.c (load_builtin_unixlib), 64-bit callers only.  This file stays
 * under the license above.
 *
 * WHY THIS EXISTS
 * ---------------
 * Resident Evil 2 (Steam 883710, log re2-ipadm4-579) plays its cutscenes through Media Foundation
 * and dies on the first one: the ASF byte stream handler's DLL, mfasfsrcsnk.dll, is not in the
 * prefix (c0000135), the source resolver has nothing, the game does not check and reads NULL.
 * mfasfsrcsnk.dll (Wine's) demuxes through winedmo.dll, whose unix side is FFmpeg's libavformat.
 * Both DLLs ship as opt-in files (tools/build-wine-extra-dlls.sh); this is winedmo's unix side,
 * on the libavformat that build/ffmpeg/build.sh builds (asf, mov, mp3, wav demuxers only).
 *
 * HOW IT TALKS TO THE PE SIDE
 * ---------------------------
 * The demuxer reads its bytes from the Media Foundation byte stream, which lives on the PE side:
 * libavformat's read and seek callbacks call back into winedmo.dll with KeUserDispatchCallback
 * (a user-mode callback on the calling thread, the way win32u calls window procedures), and the PE
 * callback answers with NtCallbackReturn.  The PE side keeps a 256 KiB read cache
 * (struct stream_context), which is read here after the callback returns.
 *
 * Log lines: "[winedmo] ..." for the demuxer's life (created, streams, ended); every line is a
 * plain dprintf(2, ...) because this unix side has no debug channel and the app runs with
 * WINEDEBUG=err+all.  Steady-state lines (read errors) are capped.
 */

#include "config.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <unistd.h>
#include <errno.h>

#include <libavutil/avutil.h>
#include <libavutil/mathematics.h>
#include <libavutil/mem.h>
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>

#include "ntstatus.h"
#define WIN32_NO_STATUS
#include "windef.h"
#include "winbase.h"
#include "winternl.h"
#include "unixlib.h"

#include "d3d9types.h"
#include "mfapi.h"

#define DMO_MAX_LOGS 200

static unsigned int dmo_log_count;

static void dmo_log( const char *fmt, ... ) __attribute__((format(printf, 1, 2)));
static void dmo_log( const char *fmt, ... )
{
    char buf[640];
    va_list args;
    int n;

    va_start( args, fmt );
    n = vsnprintf( buf, sizeof(buf), fmt, args );
    va_end( args );
    if (n <= 0) return;
    dprintf( 2, "[winedmo] %s%s", buf, buf[strlen(buf) - 1] == '\n' ? "" : "\n" );
}

/* The lines that repeat (a failing read, a failing seek) stop after DMO_MAX_LOGS. */
#define DMO_LOG_CAPPED( ... )                                                            \
    do {                                                                                  \
        unsigned int n__ = __atomic_add_fetch( &dmo_log_count, 1, __ATOMIC_RELAXED );     \
        if (n__ <= DMO_MAX_LOGS) dmo_log( __VA_ARGS__ );                                  \
        else if (n__ == DMO_MAX_LOGS + 1) dmo_log( "further repeating lines suppressed\n" ); \
    } while (0)

static UINT64 seek_callback;
static UINT64 read_callback;

static inline const char *averr( int err, char *buf, size_t size )
{
    if (av_strerror( err, buf, size ) < 0) snprintf( buf, size, "error %d", err );
    return buf;
}

/***********************************************************************
 *           the PE-side byte stream
 */
static int64_t unix_seek_callback( void *opaque, int64_t offset, int whence )
{
    struct seek_callback_params params = {.dispatch = {.callback = seek_callback}, .context = (UINT_PTR)opaque};
    struct stream_context *context = opaque;
    void *ret_ptr;
    ULONG ret_len;
    int status;

    whence &= ~AVSEEK_FORCE;   /* libavformat ORs it into a seek it insists on; the PE side has one kind of seek */
    if (whence == AVSEEK_SIZE) return context->length;
    if (whence == SEEK_END) offset += context->length;
    if (whence == SEEK_CUR) offset += context->position;
    if ((UINT64)offset > context->length) offset = context->length;

    if (offset / context->capacity != context->position / context->capacity)
    {
        for (;;)
        {
            /* seek stream to multiples of buffer capacity */
            params.offset = (offset / context->capacity) * context->capacity;
            status = KeUserDispatchCallback( &params.dispatch, sizeof(params), &ret_ptr, &ret_len );
            if (status || ret_len != sizeof(UINT64))
            {
                DMO_LOG_CAPPED( "seek callback failed: status %#x, %u bytes returned\n", status, (unsigned)ret_len );
                return AVERROR( EINVAL );
            }
            if (*(UINT64 *)ret_ptr == params.offset) break;
            offset = *(UINT64 *)ret_ptr;
        }
        context->size = 0;
    }

    context->position = offset;
    return offset;
}

static int stream_context_read( struct stream_context *context )
{
    struct read_callback_params params = {.dispatch = {.callback = read_callback}, .context = (UINT_PTR)context};
    void *ret_ptr;
    ULONG ret_len;
    int status;

    params.size = context->capacity;
    status = KeUserDispatchCallback( &params.dispatch, sizeof(params), &ret_ptr, &ret_len );
    if (status || ret_len != sizeof(ULONG))
    {
        DMO_LOG_CAPPED( "read callback failed: status %#x, %u bytes returned\n", status, (unsigned)ret_len );
        return AVERROR( EINVAL );
    }
    context->size = *(ULONG *)ret_ptr;
    return 0;
}

static int unix_read_callback( void *opaque, uint8_t *buffer, int size )
{
    struct stream_context *context = opaque;
    int ret, total = 0;

    if (!(size = min( (UINT64)size, context->length - context->position ))) return AVERROR_EOF;

    while (size)
    {
        int step, buffer_offset = context->position % context->capacity;

        if (!context->size && (ret = stream_context_read( context )) < 0) return ret;
        if (!(step = min( size, context->size - buffer_offset ))) break;
        memcpy( buffer, context->buffer + buffer_offset, step );
        buffer += step;
        total += step;
        size -= step;

        context->position += step;
        if (!(context->position % context->capacity)) context->size = 0;
    }

    if (!total) return AVERROR_EOF;
    return total;
}

/***********************************************************************
 *           media types (dlls/winedmo/unix_media_type.c)
 */
/* The GUIDs of the media types, written out and static: initguid.h would put a global definition
 * of every GUID of these headers into libntdll_unix.a, where a second definition (another unix
 * lib, the app) is a link error.  Subtypes are the base GUID with a FOURCC or D3DFMT in Data1. */
static const GUID dmo_MFMediaType_Audio =
    {0x73647561, 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71}};
static const GUID dmo_MFMediaType_Video =
    {0x73646976, 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71}};
static const GUID dmo_MEDIASUBTYPE_MPEG1Payload =
    {0xe436eb81, 0x524f, 0x11ce, {0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70}};

static GUID dmo_subtype( UINT32 data1 )
{
    GUID guid = {0x00000000, 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71}};
    guid.Data1 = data1;
    return guid;
}

#define DMO_FOURCC( a, b, c, d ) ((UINT32)(a) | ((UINT32)(b) << 8) | ((UINT32)(c) << 16) | ((UINT32)(d) << 24))

static UINT wave_format_tag_from_codec_id( enum AVCodecID id )
{
    const struct AVCodecTag *table[] = {avformat_get_riff_audio_tags(), avformat_get_mov_audio_tags(), 0};
    return av_codec_get_tag( table, id );
}

static void wave_format_ex_init( const AVCodecParameters *params, WAVEFORMATEX *format, UINT32 format_size, WORD format_tag )
{
    memset( format, 0, format_size );
    format->cbSize = format_size - sizeof(*format);
    format->wFormatTag = format_tag;
    format->nChannels = params->ch_layout.nb_channels;
    format->nSamplesPerSec = params->sample_rate;
    format->wBitsPerSample = av_get_bits_per_sample( params->codec_id );
    if (!format->wBitsPerSample) format->wBitsPerSample = params->bits_per_coded_sample;
    if (!(format->nBlockAlign = params->block_align)) format->nBlockAlign = format->wBitsPerSample * format->nChannels / 8;
    if (!(format->nAvgBytesPerSec = params->bit_rate / 8)) format->nAvgBytesPerSec = format->nSamplesPerSec * format->nBlockAlign;
}

static NTSTATUS wave_format_extensible_from_codec_params( const AVCodecParameters *params, WAVEFORMATEXTENSIBLE *format,
                                                          UINT32 *format_size, UINT wave_format_size, const GUID *subtype,
                                                          UINT64 channel_mask )
{
    UINT32 capacity = *format_size;

    *format_size = max( wave_format_size, sizeof(*format) + params->extradata_size );
    if (*format_size > capacity) return STATUS_BUFFER_TOO_SMALL;

    wave_format_ex_init( params, &format->Format, *format_size, WAVE_FORMAT_EXTENSIBLE );
    if (params->extradata_size && params->extradata) memcpy( format + 1, params->extradata, params->extradata_size );
    format->Samples.wValidBitsPerSample = 0;
    format->dwChannelMask = channel_mask;
    format->SubFormat = *subtype;
    return STATUS_SUCCESS;
}

static NTSTATUS wave_format_ex_from_codec_params( const AVCodecParameters *params, WAVEFORMATEX *format, UINT32 *format_size,
                                                  UINT32 wave_format_size, WORD format_tag )
{
    UINT32 capacity = *format_size;

    *format_size = max( wave_format_size, sizeof(*format) + params->extradata_size );
    if (*format_size > capacity) return STATUS_BUFFER_TOO_SMALL;

    wave_format_ex_init( params, format, *format_size, format_tag );
    if (params->extradata_size && params->extradata) memcpy( format + 1, params->extradata, params->extradata_size );
    return STATUS_SUCCESS;
}

static NTSTATUS heaac_wave_format_from_codec_params( const AVCodecParameters *params, HEAACWAVEINFO *format, UINT32 *format_size )
{
    UINT32 capacity = *format_size;

    *format_size = sizeof(*format) + params->extradata_size;
    if (*format_size > capacity) return STATUS_BUFFER_TOO_SMALL;

    wave_format_ex_init( params, &format->wfx, *format_size, WAVE_FORMAT_MPEG_HEAAC );
    if (params->extradata_size && params->extradata) memcpy( format + 1, params->extradata, params->extradata_size );
    format->wPayloadType = 0;
    format->wAudioProfileLevelIndication = 0;
    format->wStructType = 0;
    return STATUS_SUCCESS;
}

static NTSTATUS audio_format_from_codec_params( const AVCodecParameters *params, void *format, UINT32 *format_size )
{
    UINT format_tag, wave_format_size = sizeof(WAVEFORMATEX);
    UINT64 channel_mask;
    int channels;

    if (params->codec_id == AV_CODEC_ID_AAC) return heaac_wave_format_from_codec_params( params, format, format_size );
    if (params->codec_id == AV_CODEC_ID_MP1) wave_format_size = sizeof(MPEG1WAVEFORMAT);
    if (params->codec_id == AV_CODEC_ID_MP3) wave_format_size = sizeof(MPEGLAYER3WAVEFORMAT);
    if (params->codec_id == AV_CODEC_ID_WMAV1) wave_format_size = sizeof(MSAUDIO1WAVEFORMAT);
    if (params->codec_id == AV_CODEC_ID_WMAV2) wave_format_size = sizeof(WMAUDIO2WAVEFORMAT);
    if (params->codec_id == AV_CODEC_ID_WMAPRO) wave_format_size = sizeof(WMAUDIO3WAVEFORMAT);
    if (params->codec_id == AV_CODEC_ID_WMAVOICE) wave_format_size = sizeof(WMAUDIO3WAVEFORMAT);
    if (params->codec_id == AV_CODEC_ID_WMALOSSLESS) wave_format_size = sizeof(WMAUDIO3WAVEFORMAT);

    if (!(channels = params->ch_layout.nb_channels)) channels = 1;
    if (params->ch_layout.order != AV_CHANNEL_ORDER_NATIVE) channel_mask = 0;
    else channel_mask = params->ch_layout.u.mask;

    format_tag = wave_format_tag_from_codec_id( params->codec_id );
    if (format_tag == WAVE_FORMAT_EXTENSIBLE || format_tag >> 16 || (channels > 2 && channel_mask != 0))
    {
        GUID subtype = dmo_subtype( format_tag );
        wave_format_size += sizeof(WAVEFORMATEXTENSIBLE) - sizeof(WAVEFORMATEX);
        return wave_format_extensible_from_codec_params( params, format, format_size, wave_format_size,
                                                         &subtype, channel_mask );
    }

    return wave_format_ex_from_codec_params( params, format, format_size, wave_format_size, format_tag );
}

static GUID subtype_from_pixel_format( enum AVPixelFormat fmt )
{
    switch (fmt)
    {
    case AV_PIX_FMT_NONE: return dmo_subtype( 0 );
    case AV_PIX_FMT_YUV420P: return dmo_subtype( DMO_FOURCC( 'I', '4', '2', '0' ) );
    case AV_PIX_FMT_YUVJ420P: return dmo_subtype( DMO_FOURCC( 'I', '4', '2', '0' ) );
    case AV_PIX_FMT_YUYV422: return dmo_subtype( DMO_FOURCC( 'Y', 'U', 'Y', '2' ) );
    case AV_PIX_FMT_UYVY422: return dmo_subtype( DMO_FOURCC( 'U', 'Y', 'V', 'Y' ) );
    case AV_PIX_FMT_BGR0: return dmo_subtype( D3DFMT_X8R8G8B8 );   /* MFVideoFormat_RGB32 */
    case AV_PIX_FMT_RGBA: return dmo_subtype( D3DFMT_A8B8G8R8 );   /* MFVideoFormat_ABGR32 */
    case AV_PIX_FMT_BGRA: return dmo_subtype( D3DFMT_A8R8G8B8 );   /* MFVideoFormat_ARGB32 */
    case AV_PIX_FMT_BGR24: return dmo_subtype( D3DFMT_R8G8B8 );    /* MFVideoFormat_RGB24 */
    case AV_PIX_FMT_NV12: return dmo_subtype( DMO_FOURCC( 'N', 'V', '1', '2' ) );
    case AV_PIX_FMT_NV21: return dmo_subtype( DMO_FOURCC( 'N', 'V', '2', '1' ) );
    case AV_PIX_FMT_P010: return dmo_subtype( DMO_FOURCC( 'P', '0', '1', '0' ) );
    case AV_PIX_FMT_RGB565: return dmo_subtype( D3DFMT_R5G6B5 );   /* MFVideoFormat_RGB565 */
    case AV_PIX_FMT_RGB555: return dmo_subtype( D3DFMT_X1R5G5B5 ); /* MFVideoFormat_RGB555 */
    case AV_PIX_FMT_RGB8: return dmo_subtype( D3DFMT_P8 );         /* MFVideoFormat_RGB8 */
    default: return dmo_subtype( 0 );
    }
}

static UINT video_format_tag_from_codec_id( enum AVCodecID id )
{
    const struct AVCodecTag *table[] = {avformat_get_riff_video_tags(), avformat_get_mov_video_tags(), 0};
    return av_codec_get_tag( table, id );
}

static void mf_video_format_init( const AVCodecParameters *params, MFVIDEOFORMAT *format, UINT32 format_size,
                                  const AVRational *sar, const AVRational *fps, UINT32 align )
{
    memset( format, 0, format_size );
    format->dwSize = format_size;

    if (params->codec_id == AV_CODEC_ID_RAWVIDEO && params->format != AV_PIX_FMT_NONE)
        format->guidFormat = subtype_from_pixel_format( params->format );
    else
    {
        format->guidFormat = dmo_subtype( 0 );
        if (params->codec_id == AV_CODEC_ID_MPEG1VIDEO) format->guidFormat = dmo_MEDIASUBTYPE_MPEG1Payload;
        else if (params->codec_id == AV_CODEC_ID_H264) format->guidFormat.Data1 = DMO_FOURCC( 'H', '2', '6', '4' );
        else if (params->codec_id == AV_CODEC_ID_VP9) format->guidFormat.Data1 = DMO_FOURCC( 'V', 'P', '9', '0' );
        else if (params->codec_tag) format->guidFormat.Data1 = params->codec_tag;
        else format->guidFormat.Data1 = video_format_tag_from_codec_id( params->codec_id );
    }

    format->videoInfo.dwWidth = (params->width + align) & ~align;
    format->videoInfo.dwHeight = (params->height + align) & ~align;
    if (format->videoInfo.dwWidth != params->width || format->videoInfo.dwHeight != params->height)
    {
        format->videoInfo.MinimumDisplayAperture.Area.cx = params->width;
        format->videoInfo.MinimumDisplayAperture.Area.cy = params->height;
    }
    format->videoInfo.GeometricAperture = format->videoInfo.MinimumDisplayAperture;
    format->videoInfo.PanScanAperture = format->videoInfo.MinimumDisplayAperture;

    if (sar->num && sar->den)
    {
        format->videoInfo.PixelAspectRatio.Numerator = sar->num;
        format->videoInfo.PixelAspectRatio.Denominator = sar->den;
    }
    else
    {
        format->videoInfo.PixelAspectRatio.Numerator = 1;
        format->videoInfo.PixelAspectRatio.Denominator = 1;
    }

    if (fps->num && fps->den)
    {
        format->videoInfo.FramesPerSecond.Numerator = fps->num;
        format->videoInfo.FramesPerSecond.Denominator = fps->den;
    }
}

static NTSTATUS video_format_from_codec_params( const AVCodecParameters *params, MFVIDEOFORMAT *format, UINT32 *format_size,
                                                const AVRational *sar, const AVRational *fps, UINT32 align )
{
    UINT32 capacity = *format_size;

    *format_size = sizeof(*format) + params->extradata_size;
    if (*format_size > capacity) return STATUS_BUFFER_TOO_SMALL;

    mf_video_format_init( params, format, *format_size, sar, fps, align );
    if (params->extradata_size && params->extradata) memcpy( format + 1, params->extradata, params->extradata_size );
    return STATUS_SUCCESS;
}

static NTSTATUS media_type_from_codec_params( const AVCodecParameters *params, const AVRational *sar, const AVRational *fps,
                                              UINT32 align, struct media_type *media_type )
{
    if (params->codec_type == AVMEDIA_TYPE_AUDIO)
    {
        media_type->major = dmo_MFMediaType_Audio;
        return audio_format_from_codec_params( params, media_type->audio, &media_type->format_size );
    }

    if (params->codec_type == AVMEDIA_TYPE_VIDEO)
    {
        media_type->major = dmo_MFMediaType_Video;
        return video_format_from_codec_params( params, media_type->video, &media_type->format_size, sar, fps, align );
    }

    dmo_log( "stream type %#x (%s) has no media type\n", params->codec_type, avcodec_get_name( params->codec_id ) );
    return STATUS_NOT_IMPLEMENTED;
}

/***********************************************************************
 *           the demuxer (dlls/winedmo/unix_demuxer.c)
 */
struct demuxer
{
    pthread_mutex_t lock;
    AVFormatContext *ctx;
    AVIOContext *pb;            /* ours (custom I/O: avformat never frees it) */
    AVPacket *last_packet;      /* read, but the caller's buffer was too small for it */
    UINT64 packets[8], bytes[8];/* per stream, for the summary line */
    UINT32 stream_count;
    BOOL logged_first;
};

static struct demuxer *get_demuxer( struct winedmo_demuxer demuxer )
{
    return (struct demuxer *)(UINT_PTR)demuxer.handle;
}

static INT64 get_user_time( INT64 time, AVRational time_base )
{
    static const AVRational USER_TIME_BASE_Q = {1, 10000000};
    return av_rescale_q_rnd( time, time_base, USER_TIME_BASE_Q, AV_ROUND_PASS_MINMAX );
}

static INT64 get_stream_time( const AVStream *stream, INT64 time )
{
    if (stream->time_base.num && stream->time_base.den) return get_user_time( time, stream->time_base );
    return get_user_time( time, AV_TIME_BASE_Q );
}

static INT64 get_context_duration( const AVFormatContext *ctx )
{
    INT64 i, max_duration = AV_NOPTS_VALUE;

    for (i = 0; i < ctx->nb_streams; i++)
    {
        const AVStream *stream = ctx->streams[i];
        INT64 duration = get_stream_time( stream, stream->duration );
        if (duration == AV_NOPTS_VALUE) continue;
        if (duration >= max_duration) max_duration = duration;
        if (max_duration == AV_NOPTS_VALUE) max_duration = duration;
    }

    if (max_duration == AV_NOPTS_VALUE) return get_user_time( ctx->duration, AV_TIME_BASE_Q );
    return max_duration;
}

static NTSTATUS demuxer_check( void *arg )
{
    struct demuxer_check_params *params = arg;
    const AVInputFormat *format = NULL;

    if (!strcmp( params->mime_type, "video/mp4" )) format = av_find_input_format( "mp4" );
    else if (!strcmp( params->mime_type, "video/avi" )) format = av_find_input_format( "avi" );
    else if (!strcmp( params->mime_type, "audio/wav" )) format = av_find_input_format( "wav" );
    else if (!strcmp( params->mime_type, "audio/x-ms-wma" )) format = av_find_input_format( "asf" );
    else if (!strcmp( params->mime_type, "video/x-ms-wmv" )) format = av_find_input_format( "asf" );
    else if (!strcmp( params->mime_type, "video/x-ms-asf" )) format = av_find_input_format( "asf" );
    else if (!strcmp( params->mime_type, "video/mpeg" )) format = av_find_input_format( "mpeg" );
    else if (!strcmp( params->mime_type, "audio/mp3" )) format = av_find_input_format( "mp3" );

    DMO_LOG_CAPPED( "demuxer_check mime=%s -> %s\n", params->mime_type, format ? format->name : "not supported" );
    return format ? STATUS_SUCCESS : STATUS_NOT_SUPPORTED;
}

static void demuxer_free( struct demuxer *demuxer )
{
    if (demuxer->ctx) avformat_free_context( demuxer->ctx );
    if (demuxer->pb)
    {
        /* the buffer may have been reallocated by the probe; it is not freed with the context */
        av_freep( &demuxer->pb->buffer );
        avio_context_free( &demuxer->pb );
    }
    av_packet_free( &demuxer->last_packet );
    pthread_mutex_destroy( &demuxer->lock );
    free( demuxer );
}

static NTSTATUS demuxer_create( void *arg )
{
    struct demuxer_create_params *params = arg;
    const char *ext = params->url ? strrchr( params->url, '.' ) : "";
    char err[AV_ERROR_MAX_STRING_SIZE];
    const AVInputFormat *format;
    struct demuxer *demuxer;
    unsigned char *buffer;
    int i, ret;

    if (!ext) ext = "";
    if (!(demuxer = calloc( 1, sizeof(*demuxer) ))) return STATUS_NO_MEMORY;
    pthread_mutex_init( &demuxer->lock, NULL );
    if (!(demuxer->ctx = avformat_alloc_context())) goto failed;
    if (!(buffer = av_malloc( 64 * 1024 ))) goto failed;
    if (!(demuxer->pb = avio_alloc_context( buffer, 64 * 1024, 0, params->context, unix_read_callback, NULL,
                                            unix_seek_callback )))
    {
        av_free( buffer );
        goto failed;
    }
    demuxer->ctx->pb = demuxer->pb;

    if ((ret = avformat_open_input( &demuxer->ctx, NULL, NULL, NULL )) < 0)
    {
        /* avformat_open_input frees the format context on failure and leaves demuxer->ctx NULL */
        dmo_log( "avformat_open_input failed: %s (url %s)\n", averr( ret, err, sizeof(err) ),
                 params->url ? params->url : "(none)" );
        goto failed;
    }
    format = demuxer->ctx->iformat;

    if ((params->duration = get_context_duration( demuxer->ctx )) == AV_NOPTS_VALUE ||
        strstr( format->name, "mp3" ))
    {
        if ((ret = avformat_find_stream_info( demuxer->ctx, NULL )) < 0)
        {
            dmo_log( "avformat_find_stream_info failed: %s\n", averr( ret, err, sizeof(err) ) );
            goto failed;
        }
        params->duration = get_context_duration( demuxer->ctx );
    }
    if (demuxer->ctx->nb_streams > ARRAY_SIZE(demuxer->packets))
        dmo_log( "%u streams; the packet counts below cover the first %u\n", demuxer->ctx->nb_streams,
                 (unsigned)ARRAY_SIZE(demuxer->packets) );

    demuxer->stream_count = params->stream_count = demuxer->ctx->nb_streams;
    params->demuxer.handle = (UINT_PTR)demuxer;
    if (strstr( format->name, "mp4" )) strcpy( params->mime_type, "video/mp4" );
    else if (strstr( format->name, "avi" )) strcpy( params->mime_type, "video/avi" );
    else if (strstr( format->name, "mpeg" )) strcpy( params->mime_type, "video/mpeg" );
    else if (strstr( format->name, "mp3" )) strcpy( params->mime_type, "audio/mp3" );
    else if (strstr( format->name, "wav" )) strcpy( params->mime_type, "audio/wav" );
    else if (strstr( format->name, "asf" ))
    {
        if (!strcmp( ext, ".wma" )) strcpy( params->mime_type, "audio/x-ms-wma" );
        else if (!strcmp( ext, ".wmv" )) strcpy( params->mime_type, "video/x-ms-wmv" );
        else strcpy( params->mime_type, "video/x-ms-asf" );
    }
    else
    {
        dmo_log( "unknown MIME type for format %s, url %s\n", format->name, params->url ? params->url : "(none)" );
        strcpy( params->mime_type, "video/x-application" );
    }

    dmo_log( "demuxer created: format=%s (%s) mime=%s streams=%u duration=%lld (100 ns) size=%llu url=%s\n",
             format->name, format->long_name, params->mime_type, params->stream_count, (long long)params->duration,
             (unsigned long long)params->context->length, params->url ? params->url : "(none)" );
    for (i = 0; i < demuxer->ctx->nb_streams; i++)
    {
        const AVCodecParameters *par = demuxer->ctx->streams[i]->codecpar;
        if (par->codec_type == AVMEDIA_TYPE_VIDEO)
            dmo_log( "  stream %d: video %s tag=%#x %dx%d fps=%d/%d extradata=%d bytes\n", i, avcodec_get_name( par->codec_id ),
                     par->codec_tag, par->width, par->height, demuxer->ctx->streams[i]->avg_frame_rate.num,
                     demuxer->ctx->streams[i]->avg_frame_rate.den, par->extradata_size );
        else if (par->codec_type == AVMEDIA_TYPE_AUDIO)
            dmo_log( "  stream %d: audio %s tag=%#x %d Hz %d ch block=%d bitrate=%lld extradata=%d bytes\n", i,
                     avcodec_get_name( par->codec_id ), par->codec_tag, par->sample_rate, par->ch_layout.nb_channels,
                     par->block_align, (long long)par->bit_rate, par->extradata_size );
        else
            dmo_log( "  stream %d: type %d %s\n", i, par->codec_type, avcodec_get_name( par->codec_id ) );
    }
    return STATUS_SUCCESS;

failed:
    demuxer_free( demuxer );
    return STATUS_UNSUCCESSFUL;
}

static NTSTATUS demuxer_destroy( void *arg )
{
    struct demuxer_destroy_params *params = arg;
    struct demuxer *demuxer = get_demuxer( params->demuxer );
    char line[256];
    size_t len = 0;
    UINT i;

    if (!demuxer) return STATUS_INVALID_HANDLE;
    params->context = demuxer->pb->opaque;
    line[0] = 0;
    for (i = 0; i < min( demuxer->stream_count, ARRAY_SIZE(demuxer->packets) ) && len < sizeof(line) - 48; i++)
        len += snprintf( line + len, sizeof(line) - len, " %u=%llu (%llu bytes)", i,
                         (unsigned long long)demuxer->packets[i], (unsigned long long)demuxer->bytes[i] );
    dmo_log( "demuxer destroyed: packets read per stream:%s\n", line );
    demuxer_free( demuxer );
    return STATUS_SUCCESS;
}

static NTSTATUS demuxer_read( void *arg )
{
    struct demuxer_read_params *params = arg;
    struct demuxer *demuxer = get_demuxer( params->demuxer );
    struct sample *sample = &params->sample;
    UINT capacity = params->sample.size;
    char err[AV_ERROR_MAX_STRING_SIZE];
    AVPacket *packet;
    AVStream *stream;
    NTSTATUS status = STATUS_SUCCESS;
    int ret;

    if (!demuxer) return STATUS_INVALID_HANDLE;
    pthread_mutex_lock( &demuxer->lock );
    if (!(packet = demuxer->last_packet))
    {
        if (!(packet = av_packet_alloc()))
        {
            pthread_mutex_unlock( &demuxer->lock );
            return STATUS_NO_MEMORY;
        }
        if ((ret = av_read_frame( demuxer->ctx, packet )) < 0)
        {
            av_packet_free( &packet );
            pthread_mutex_unlock( &demuxer->lock );
            if (ret == AVERROR_EOF) return STATUS_END_OF_FILE;
            DMO_LOG_CAPPED( "av_read_frame failed: %s\n", averr( ret, err, sizeof(err) ) );
            return STATUS_UNSUCCESSFUL;
        }
    }

    params->sample.size = packet->size;
    if (capacity < packet->size)
    {
        demuxer->last_packet = packet;
        pthread_mutex_unlock( &demuxer->lock );
        return STATUS_BUFFER_TOO_SMALL;
    }

    stream = demuxer->ctx->streams[packet->stream_index];
    sample->pts = get_stream_time( stream, packet->pts );
    sample->dts = get_stream_time( stream, packet->dts );
    sample->duration = get_stream_time( stream, packet->duration );
    if (packet->flags & AV_PKT_FLAG_KEY) sample->flags |= SAMPLE_FLAG_SYNC_POINT;
    memcpy( (void *)(UINT_PTR)sample->data, packet->data, packet->size );
    params->stream = packet->stream_index;
    if ((UINT)packet->stream_index < ARRAY_SIZE(demuxer->packets))
    {
        demuxer->packets[packet->stream_index]++;
        demuxer->bytes[packet->stream_index] += packet->size;
    }
    if (!demuxer->logged_first)
    {
        demuxer->logged_first = TRUE;
        dmo_log( "first packet: stream %d, %d bytes, pts=%lld dts=%lld (100 ns)\n", packet->stream_index, packet->size,
                 (long long)sample->pts, (long long)sample->dts );
    }
    av_packet_free( &packet );
    demuxer->last_packet = NULL;
    pthread_mutex_unlock( &demuxer->lock );

    return status;
}

static NTSTATUS demuxer_seek( void *arg )
{
    struct demuxer_seek_params *params = arg;
    struct demuxer *demuxer = get_demuxer( params->demuxer );
    int64_t timestamp = params->timestamp * AV_TIME_BASE / 10000000;
    char err[AV_ERROR_MAX_STRING_SIZE];
    int ret;

    if (!demuxer) return STATUS_INVALID_HANDLE;
    pthread_mutex_lock( &demuxer->lock );
    ret = avformat_seek_file( demuxer->ctx, -1, 0, timestamp, timestamp, 0 );
    if (ret >= 0) av_packet_free( &demuxer->last_packet );
    pthread_mutex_unlock( &demuxer->lock );
    if (ret < 0)
    {
        DMO_LOG_CAPPED( "seek to %lld (100 ns) failed: %s\n", (long long)params->timestamp, averr( ret, err, sizeof(err) ) );
        return STATUS_UNSUCCESSFUL;
    }
    DMO_LOG_CAPPED( "seek to %lld (100 ns)\n", (long long)params->timestamp );
    return STATUS_SUCCESS;
}

static NTSTATUS demuxer_stream_lang( void *arg )
{
    struct demuxer_stream_lang_params *params = arg;
    struct demuxer *demuxer = get_demuxer( params->demuxer );
    AVStream *stream;
    const AVDictionaryEntry *tag;

    if (!demuxer || params->stream >= demuxer->ctx->nb_streams) return STATUS_NOT_FOUND;
    stream = demuxer->ctx->streams[params->stream];
    if (!(tag = av_dict_get( stream->metadata, "language", NULL, AV_DICT_IGNORE_SUFFIX ))) return STATUS_NOT_FOUND;

    lstrcpynA( params->buffer, tag->value, ARRAY_SIZE( params->buffer ) );
    return STATUS_SUCCESS;
}

static NTSTATUS demuxer_stream_name( void *arg )
{
    struct demuxer_stream_name_params *params = arg;
    struct demuxer *demuxer = get_demuxer( params->demuxer );
    AVStream *stream;
    const AVDictionaryEntry *tag;

    if (!demuxer || params->stream >= demuxer->ctx->nb_streams) return STATUS_NOT_FOUND;
    stream = demuxer->ctx->streams[params->stream];
    if (!(tag = av_dict_get( stream->metadata, "title", NULL, AV_DICT_IGNORE_SUFFIX ))) return STATUS_NOT_FOUND;

    lstrcpynA( params->buffer, tag->value, ARRAY_SIZE( params->buffer ) );
    return STATUS_SUCCESS;
}

static NTSTATUS demuxer_stream_type( void *arg )
{
    struct demuxer_stream_type_params *params = arg;
    struct demuxer *demuxer = get_demuxer( params->demuxer );
    AVStream *stream;

    if (!demuxer || params->stream >= demuxer->ctx->nb_streams) return STATUS_NOT_FOUND;
    stream = demuxer->ctx->streams[params->stream];
    return media_type_from_codec_params( stream->codecpar, &stream->sample_aspect_ratio, &stream->avg_frame_rate, 0,
                                         &params->media_type );
}

/***********************************************************************
 *           the unix call table
 */
static void dmo_av_log( void *avcl, int level, const char *fmt, va_list args )
{
    static unsigned int count;
    char buf[512];
    int n;

    if (level > AV_LOG_WARNING) return;
    if (__atomic_add_fetch( &count, 1, __ATOMIC_RELAXED ) > 64) return;
    n = vsnprintf( buf, sizeof(buf), fmt, args );
    if (n <= 0) return;
    dprintf( 2, "[winedmo] av: %s%s", buf, buf[strlen(buf) - 1] == '\n' ? "" : "\n" );
}

static NTSTATUS process_attach( void *arg )
{
    struct process_attach_params *params = arg;

    seek_callback = params->seek_callback;
    read_callback = params->read_callback;
    dmo_log( "process attached: libavformat %u.%u.%u, callbacks seek=%#llx read=%#llx\n",
             AV_VERSION_MAJOR( avformat_version() ), AV_VERSION_MINOR( avformat_version() ),
             AV_VERSION_MICRO( avformat_version() ), (unsigned long long)seek_callback,
             (unsigned long long)read_callback );
    av_log_set_callback( dmo_av_log );
    return STATUS_SUCCESS;
}

/* The index layout is dlls/winedmo/unixlib.h `enum unix_funcs`, entry for entry. */
const unixlib_entry_t __wine_unix_call_funcs[] =
{
#define X( name ) [unix_##name] = name
    X( process_attach ),

    X( demuxer_check ),
    X( demuxer_create ),
    X( demuxer_destroy ),
    X( demuxer_read ),
    X( demuxer_seek ),
    X( demuxer_stream_lang ),
    X( demuxer_stream_name ),
    X( demuxer_stream_type ),
#undef X
};

C_ASSERT( ARRAY_SIZE(__wine_unix_call_funcs) == unix_funcs_count );
