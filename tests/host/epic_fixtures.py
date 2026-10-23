# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Synthetic Epic builds for tests/host/check-epic.py: a binary manifest, chunk files and a local CDN.

Everything here is made up by the test from the layout the design records (docs/research/open-fork-
2026-10-18/epic.md section 2.3): no byte of any real build, and no code of any other Epic client. The
writers are an independent second implementation of the format as the design describes it, so they pin
BCD's reader to that description; whether the description is right is what the first install on a device
shows.
"""
import hashlib
import http.server
import random
import re
import socketserver
import struct
import threading
import time
import zlib


def fstring(text):
    if text == "":
        return struct.pack("<i", 0)
    raw = text.encode("latin-1") + b"\0"
    return struct.pack("<i", len(raw)) + raw


def fstring16(text):
    raw = text.encode("utf-16-le") + b"\0\0"
    return struct.pack("<i", -(len(raw) // 2)) + raw


def section(version, payload):
    return struct.pack("<IB", 5 + len(payload), version) + payload


class Build:
    """A build: chunks (each a window of bytes) and files made of slices of them."""

    def __init__(self, seed=1, level=17, app="Fixture", exe="Game\\Binaries\\Win64\\Game.exe", command="-nosplash"):
        self.rng = random.Random(seed)
        self.level = level
        self.app = app
        self.exe = exe
        self.command = command
        self.prereq = ("", "", "")
        self.chunks = []          # dicts: guid(4 words), rolling, data, group, hv, compress, stored_as
        self.files = []           # dicts: name, symlink, pieces [(chunk, offset, length)], flags, tags
        self.expected = {}        # relative path (as the plan spells it) -> bytes
        self.meta_version = 1
        self.fml_version = 2
        self.utf16_names = False
        # What the chunk list's file sizes say: "file" (the chunk file's length), "body" (without its header)
        # or "plus" (a hundred bytes too many): BCD settles against the header's own length either way.
        self.size_mode = "file"
        self.bad_file_sha = None

    # -- data
    def blob(self, n, compressible=True):
        if compressible:
            words = [bytes(self.rng.choice(b"abcdefghij") for _ in range(self.rng.randint(3, 9))) for _ in range(64)]
            out = bytearray()
            while len(out) < n:
                out += self.rng.choice(words) + b" "
            return bytes(out[:n])
        return self.rng.randbytes(n)

    def chunk(self, data, hv=3, compress=True, group=None, stored_as=None):
        guid = tuple(self.rng.getrandbits(32) for _ in range(4))
        rolling = self.rng.getrandbits(64)
        self.chunks.append(dict(guid=guid, rolling=rolling, data=data, group=self.rng.randrange(100) if group is None else group,
                                hv=hv, compress=compress, stored_as=stored_as))
        return len(self.chunks) - 1

    def file(self, name, pieces, flags=0, symlink="", tags=(), canonical=None):
        """`canonical`: the name the plan spells (folders take their first spelling), when it differs."""
        data = b"" if symlink else b"".join(self.chunks[c]["data"][o:o + n] for c, o, n in pieces)
        self.files.append(dict(name=name, pieces=list(pieces), flags=flags, symlink=symlink, tags=list(tags), data=data))
        if not symlink:
            self.expected[canonical or name] = data
        return len(self.files) - 1

    # -- the chunk files and their paths
    @staticmethod
    def guid_bytes(guid):
        return struct.pack("<IIII", *guid)

    @staticmethod
    def guid_hex(guid):
        return "".join("%08X" % w for w in guid)

    def chunk_path(self, i):
        c = self.chunks[i]
        directory = "ChunksV4" if self.level >= 15 else "ChunksV3" if self.level >= 6 else "ChunksV2"
        return "%s/%02d/%016X_%s.chunk" % (directory, c["group"], c["rolling"], self.guid_hex(c["guid"]))

    def cdl_size(self, i):
        whole = len(self.chunk_file(i))
        if self.size_mode == "body":
            return whole - {1: 41, 2: 62, 3: 66, 4: 98}[self.chunks[i]["hv"]]
        return whole + 100 if self.size_mode == "plus" else whole

    def chunk_file(self, i):
        c = self.chunks[i]
        data = c["data"]
        body = zlib.compress(data, 6) if c["compress"] else data
        stored_as = c["stored_as"] if c["stored_as"] is not None else (1 if c["compress"] else 0)
        hv = c["hv"]
        size = {1: 41, 2: 62, 3: 66, 4: 98}[hv]
        head = struct.pack("<IIII", 0xB1FE3AA2, hv, size, len(body)) + self.guid_bytes(c["guid"]) + struct.pack("<QB", c["rolling"], stored_as)
        if hv >= 2:
            head += hashlib.sha1(data).digest() + bytes([2])
        if hv >= 3:
            head += struct.pack("<I", len(data))
        if hv >= 4:
            head += bytes(32)
        assert len(head) == size
        return head + body

    # -- the manifest
    def body(self):
        meta = struct.pack("<I", self.level) + struct.pack("<BI", 1, 7)
        meta += fstring(self.app) + fstring("1.2.3-fixture") + fstring(self.exe) + fstring(self.command)
        meta += struct.pack("<I", 1) + fstring("prereq-id")
        meta += fstring(self.prereq[0]) + fstring(self.prereq[1]) + fstring(self.prereq[2])
        if self.meta_version >= 1:
            meta += fstring("build-id-fixture")
        n = len(self.chunks)
        cdl = struct.pack("<I", n)
        cdl += b"".join(self.guid_bytes(c["guid"]) for c in self.chunks)
        cdl += b"".join(struct.pack("<Q", c["rolling"]) for c in self.chunks)
        cdl += b"".join(hashlib.sha1(c["data"]).digest() for c in self.chunks)
        cdl += bytes(c["group"] for c in self.chunks)
        cdl += b"".join(struct.pack("<I", len(c["data"])) for c in self.chunks)
        cdl += b"".join(struct.pack("<q", self.cdl_size(i)) for i, _ in enumerate(self.chunks))
        f = self.files
        string = fstring16 if self.utf16_names else fstring
        fml = struct.pack("<I", len(f))
        fml += b"".join(string(x["name"]) for x in f)
        fml += b"".join(fstring(x["symlink"]) for x in f)
        fml += b"".join((b"\x01" * 20) if x["name"] == self.bad_file_sha else hashlib.sha1(x["data"]).digest() for x in f)
        fml += bytes(x["flags"] for x in f)
        fml += b"".join(struct.pack("<I", len(x["tags"])) + b"".join(fstring(t) for t in x["tags"]) for x in f)
        for x in f:
            fml += struct.pack("<I", len(x["pieces"]))
            for c, o, n_ in x["pieces"]:
                fml += struct.pack("<I", 28) + self.guid_bytes(self.chunks[c]["guid"]) + struct.pack("<II", o, n_)
        if self.fml_version >= 1:
            fml += b"".join(struct.pack("<I", 1) + hashlib.md5(x["data"]).digest() for x in f)
            fml += b"".join(fstring("application/octet-stream") for _ in f)
        if self.fml_version >= 2:
            fml += b"".join(hashlib.sha256(x["data"]).digest() for x in f)
        custom = struct.pack("<I", 1) + fstring("ProductId") + fstring("fixture")
        return section(self.meta_version, meta) + section(0, cdl) + section(self.fml_version, fml) + section(0, custom)

    def manifest(self, level=None, stored_as=1, body=None, sha=None):
        body = self.body() if body is None else body
        stored = zlib.compress(body, 6) if stored_as & 1 else body
        sha = hashlib.sha1(body).digest() if sha is None else sha
        head = struct.pack("<IIII", 0x44BEC00C, 41, len(body), len(stored)) + sha + bytes([stored_as]) + struct.pack("<I", self.level if level is None else level)
        assert len(head) == 41
        return head + stored


# ---- the local CDN ------------------------------------------------------------------------------
class Cdn:
    """Serves chunk files and manifests under /<label>/<path>. The label picks the behaviour:
    good*, corrupt* (the first answer for each chunk has a flipped byte), missing* (404), refuse* (403),
    slow* (a pause per chunk), badlength* (a header that lies about the body's size), cut* (the body stops
    short once per chunk). /_log returns the requests as lines, /_reset clears them."""

    def __init__(self):
        self.lock = threading.Lock()
        self.files = {}      # path (after the label) -> bytes
        self.log = []        # (label, path, range)
        self.seen = set()
        cdn = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def reply(self, status, body=b"", headers=()):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                for k, v in headers:
                    self.send_header(k, v)
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/_log":
                    with cdn.lock:
                        text = "\n".join("%s %s %s" % (l, p, r or "-") for l, p, r in cdn.log)
                    return self.reply(200, text.encode())
                if path == "/_reset":
                    with cdn.lock:
                        cdn.log.clear()
                        cdn.seen.clear()
                    return self.reply(200, b"ok")
                parts = path.split("/", 2)
                if len(parts) < 3:
                    return self.reply(404)
                label, rest = parts[1], "/" + parts[2]
                rng = self.headers.get("Range")
                with cdn.lock:
                    cdn.log.append((label, rest, rng))
                    first = (label, rest) not in cdn.seen
                    cdn.seen.add((label, rest))
                body = cdn.files.get(rest)
                mode = label.rstrip("0123456789")
                if body is None or mode == "missing":
                    return self.reply(404)
                if mode == "refuse":
                    return self.reply(403)
                if mode == "slow":
                    time.sleep(0.08)
                if mode == "corrupt" and first and rest.endswith(".chunk"):
                    mid = len(body) // 2
                    body = body[:mid] + bytes([body[mid] ^ 0xFF]) + body[mid + 1:]
                if mode == "cut" and first and rest.endswith(".chunk") and not rng:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()
                    self.wfile.write(body[:len(body) // 2])
                    self.wfile.flush()
                    time.sleep(0.3)
                    self.close_connection = True
                    return
                if rng:
                    m = re.fullmatch(r"bytes=(\d+)-", rng)
                    start = int(m.group(1)) if m else 0
                    return self.reply(206, body[start:], [("Content-Range", "bytes %d-%d/%d" % (start, len(body) - 1, len(body))), ("Accept-Ranges", "bytes")])
                self.reply(200, body, [("Accept-Ranges", "bytes")])

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

            def handle_error(self, request, client_address):
                pass

        # Three listeners on three ports: a host is its name and port, and the engine blames a host that fails.
        self.servers = [Server(("127.0.0.1", 0), Handler) for _ in range(3)]
        self.ports = [x.server_address[1] for x in self.servers]
        self.port = self.ports[0]
        self.threads = [threading.Thread(target=x.serve_forever, daemon=True) for x in self.servers]

    def start(self):
        for t in self.threads:
            t.start()

    def stop(self):
        for x in self.servers:
            x.shutdown()
            x.server_close()

    def base(self, label, which=0):
        return "http://127.0.0.1:%d/%s" % (self.ports[which], label)

    def publish(self, build, prefix="/Builds/Org/o-fixture/ITEM/default", manifest=None):
        """The build's chunks under prefix, its manifest at prefix/<sha1>.manifest; returns the manifest's name."""
        data = build.manifest() if manifest is None else manifest
        name = hashlib.sha1(data).hexdigest() + ".manifest"
        for i in range(len(build.chunks)):
            self.files[prefix + "/" + build.chunk_path(i)] = build.chunk_file(i)
        self.files[prefix + "/" + name] = data
        return prefix, name


def main_build(seed=20261022):
    b = Build(seed)
    c0 = b.chunk(b.blob(400_000))
    c1 = b.chunk(b.blob(400_000))
    c2 = b.chunk(b.blob(123_457))
    c3 = b.chunk(b.blob(300_000, compressible=False), compress=False)
    c4 = b.chunk(b.blob(200_000), hv=1)
    c5 = b.chunk(b.blob(200_000), hv=2)
    c6 = b.chunk(b.blob(150_000), hv=4)
    c7 = b.chunk(b.blob(10_000))
    b.file("Game/Binaries/Win64/Game.exe", [(c0, 0, 400_000), (c1, 0, 400_000), (c2, 0, 123_457)], flags=4)
    b.file("Game/Binaries/Win64/EOSSDK-Win64-Shipping.dll", [(c7, 0, 10_000)])
    b.file("Game/Content/Paks/data.pak", [(c3, 0, 300_000), (c4, 0, 200_000), (c5, 0, 200_000)])
    b.file("Game/Content/Extra/a.bin", [(c6, 0, 50_000)])
    b.file("game/content/Extra/b.bin", [(c6, 50_000, 100_000)], canonical="Game/Content/Extra/b.bin")     # spelled differently: one folder
    b.file("Game/Content/shared1.bin", [(c7, 0, 10_000)])
    b.file("Game/Content/shared2.bin", [(c7, 2_000, 5_000)])
    b.file("Game/Content/slice.bin", [(c0, 100_000, 200_000)])
    b.file("Game/Saved/empty.txt", [])
    b.file("Game/link", [], symlink="Game/Content")
    b.file("Game/Content/zero-part.bin", [(c7, 0, 0), (c7, 0, 10)])
    return b


# ---- what check-epic.py writes for the Swift harness ---------------------------------------------
def write_tree(root, files):
    import os
    for rel, data in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as out:
            out.write(data)


def hostile_bodies(b):
    """Hash-valid manifests with a body that lies: each must be refused or read safely, never crash."""
    good = b.body()
    out = {}
    rng = random.Random(77)

    # Counts that cannot be true.
    def with_cdl_count(count):
        body = bytearray(good)
        meta_size = struct.unpack_from("<I", body, 0)[0]
        struct.pack_into("<I", body, meta_size + 5, count)
        return bytes(body)
    for name, count in [("cdl-huge", 0xFFFFFFFF), ("cdl-over", 0x7FFFFFFF), ("cdl-more", len(b.chunks) + 1), ("cdl-zero", 0)]:
        out[name] = b.manifest(body=with_cdl_count(count))
    def with_fml_count(count):
        body = bytearray(good)
        meta_size = struct.unpack_from("<I", body, 0)[0]
        cdl_size = struct.unpack_from("<I", body, meta_size)[0]
        struct.pack_into("<I", body, meta_size + cdl_size + 5, count)
        return bytes(body)
    for name, count in [("fml-huge", 0xFFFFFFFF), ("fml-over", 0x7FFFFFFF), ("fml-more", len(b.files) + 1), ("fml-zero", 0)]:
        out[name] = b.manifest(body=with_fml_count(count))
    # Section sizes that lie.
    for name, delta in [("meta-short", -3), ("meta-long", 99999)]:
        body = bytearray(good)
        struct.pack_into("<I", body, 0, max(0, struct.unpack_from("<I", body, 0)[0] + delta))
        out[name] = b.manifest(body=bytes(body))
    # A part naming a chunk the list does not hold, and one past its window.
    body = bytearray(good)
    guid = b.guid_bytes(b.chunks[0]["guid"])
    at = bytes(body).rfind(struct.pack("<I", 28) + guid)
    assert at > 0
    body[at + 4:at + 20] = bytes(16)
    out["part-unknown-guid"] = b.manifest(body=bytes(body))
    body = bytearray(good)
    struct.pack_into("<I", body, at + 20, 0xFFFFFFF0)
    out["part-offset"] = b.manifest(body=bytes(body))
    body = bytearray(good)
    struct.pack_into("<I", body, at + 24, 0xFFFFFFF0)
    out["part-size"] = b.manifest(body=bytes(body))
    body = bytearray(good)
    struct.pack_into("<I", body, at, 3)
    out["part-size-field"] = b.manifest(body=bytes(body))
    # A string with a length far past the data.
    body = bytearray(good)
    struct.pack_into("<i", body, 4 + 1 + 4 + 1 + 4, 0x7FFFFFFF)
    out["string-huge"] = b.manifest(body=bytes(body))
    body = bytearray(good)
    struct.pack_into("<i", body, 4 + 1 + 4 + 1 + 4, -0x7FFFFFFF)
    out["string-huge-wide"] = b.manifest(body=bytes(body))
    # Random damage to the body (the hash is made again, so the sections see it).
    for i in range(240):
        body = bytearray(good)
        for _ in range(rng.choice([1, 1, 2, 4, 8])):
            body[rng.randrange(len(body))] = rng.randrange(256)
        out["fuzz-%03d" % i] = b.manifest(body=bytes(body))
    return {k: v for k, v in out.items() if v is not None}


def variant_builds():
    """The manifests the parser checks, by name (bytes), from the main build with one thing changed."""
    out = {}
    b = main_build()
    out["main"] = b.manifest()
    m = main_build(); m.meta_version = 0; out["meta0"] = m.manifest()
    m = main_build(); m.fml_version = 0; out["fml0"] = m.manifest()
    m = main_build(); m.fml_version = 1; out["fml1"] = m.manifest()
    m = main_build(); m.files[10]["name"] = "Spiel/Gr\u00f6\u00dfe.bin"; m.utf16_names = True; out["utf16"] = m.manifest()
    out["newer"] = b.manifest(level=22)
    out["encrypted"] = b.manifest(stored_as=3)
    good = b.manifest()
    out["badhash"] = good[:20] + bytes([good[20] ^ 1]) + good[21:]
    out["magic"] = b"\0\0\0\0" + good[4:]
    m = main_build(); m.file("Game/EasyAntiCheat/EasyAntiCheat_Setup.exe", [(7, 0, 100)]); out["ac"] = m.manifest()
    m = main_build(); m.exe = ""; out["noexe"] = m.manifest()
    return out


def build_info(build, prefix, name):
    return dict(prefix=prefix, manifest=name, sha1=name.split(".")[0], level=build.level,
                locators=[build.chunk_path(i) for i in range(len(build.chunks))],
                wireSizes=[build.cdl_size(i) for i in range(len(build.chunks))],
                windows=[len(c["data"]) for c in build.chunks], expected="expected/" + prefix.split("/")[-2],
                paths=sorted(build.expected))


def write_fixtures(work, cdn):
    """Writes the files the Swift harness reads under `work` and publishes the builds on `cdn`; returns the fixture dict."""
    import os
    fixture = dict(cdn="http://127.0.0.1:%d" % cdn.ports[0], cdn2="http://127.0.0.1:%d" % cdn.ports[1], cdn3="http://127.0.0.1:%d" % cdn.ports[2],
                   dead="http://127.0.0.1:1", work=work, builds={}, variants={}, chunkFiles=[], hostile=[], fuzz=[])
    # The CDN's builds (each under its own folder, as a game's item folder is).
    specs = {
        "main": lambda: main_build(),
        "body": lambda: (lambda b: (setattr(b, "size_mode", "body"), b)[1])(main_build(11)),
        "plus": lambda: (lambda b: (setattr(b, "size_mode", "plus"), b)[1])(main_build(12)),
        "badfile": lambda: (lambda b: (setattr(b, "bad_file_sha", "Game/Content/slice.bin"), b)[1])(main_build(13)),
        "encrypted": lambda: (lambda b: (b.chunks[2].update(stored_as=3), b)[1])(main_build(14)),
    }
    for name, make in specs.items():
        b = make()
        prefix, manifest = cdn.publish(b, prefix="/Builds/Org/o-fixture/%s/default" % name.upper())
        fixture["builds"][name] = build_info(b, prefix, manifest)
        write_tree(os.path.join(work, "expected", name.upper()), b.expected)
        if name == "main":
            for i in range(len(b.chunks)):
                write_tree(work, {"chunks/c%d.wire" % i: b.chunk_file(i), "chunks/c%d.plain" % i: b.chunks[i]["data"]})
                stored_as = b.chunks[i]["stored_as"] if b.chunks[i]["stored_as"] is not None else (1 if b.chunks[i]["compress"] else 0)
                fixture["chunkFiles"].append(dict(wire="chunks/c%d.wire" % i, plain="chunks/c%d.plain" % i, hv=b.chunks[i]["hv"], storedAs=stored_as))
    # A build whose manifest says a newer format: published, so a download meets it.
    n = main_build(15)
    data = n.manifest(level=22)
    prefix, manifest = cdn.publish(n, prefix="/Builds/Org/o-fixture/NEWER/default", manifest=data)
    fixture["builds"]["newer"] = build_info(n, prefix, manifest)
    variants = variant_builds()
    variants["scale"] = scale_manifest()
    for name, data in variants.items():
        write_tree(work, {"manifests/%s.bin" % name: data})
        fixture["variants"][name] = "manifests/%s.bin" % name
    for name, data in hostile_bodies(main_build()).items():
        group = "fuzz" if name.startswith("fuzz-") else "hostile"
        write_tree(work, {"%s/%s.bin" % (group, name): data})
        fixture[group].append("%s/%s.bin" % (group, name))
    return fixture


def scale_manifest(files=20_000, parts_per_file=3, seed=7):
    """A manifest of a large game's order (60,000 chunks, 20,000 files), made without any chunk data: the reader and the plan
    must take it in a few seconds and a bounded amount of memory."""
    rng = random.Random(seed)
    n = files * parts_per_file
    guids = [struct.pack("<IIII", *(rng.getrandbits(32) for _ in range(4))) for _ in range(n)]
    cdl = [struct.pack("<I", n), b"".join(guids), rng.randbytes(8 * n), rng.randbytes(20 * n), bytes(rng.randrange(100) for _ in range(n)),
           struct.pack("<I", 1 << 20) * n, b"".join(struct.pack("<q", 400_000 + rng.randrange(600_000)) for _ in range(n))]
    names = ["Game/Content/Paks/chunk%05d/file%05d.%s" % (i // 500, i, "exe" if i == 0 else "pak") for i in range(files)]
    fml = [struct.pack("<I", files), b"".join(fstring(x) for x in names), fstring("") * files, rng.randbytes(20 * files), bytes(files),
           struct.pack("<I", 0) * files]
    part = struct.pack("<I", 28)
    tail = struct.pack("<II", 0, 1 << 20)
    at = 0
    for i in range(files):
        fml.append(struct.pack("<I", parts_per_file))
        for _ in range(parts_per_file):
            fml.append(part + guids[at] + tail)
            at += 1
    meta = struct.pack("<I", 17) + struct.pack("<BI", 1, 7) + fstring("Scale") + fstring("9.9") + fstring(names[0]) + fstring("")
    meta += struct.pack("<I", 0) + fstring("") + fstring("") + fstring("") + fstring("b")
    body = section(1, meta) + section(0, b"".join(cdl)) + section(2, b"".join(fml)) + section(0, struct.pack("<I", 0))
    stored = zlib.compress(body, 1)
    head = struct.pack("<IIII", 0x44BEC00C, 41, len(body), len(stored)) + hashlib.sha1(body).digest() + bytes([1]) + struct.pack("<I", 17)
    return head + stored
