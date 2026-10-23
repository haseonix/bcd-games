// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import CryptoKit
import Foundation
import zlib

// Checksums and zlib for the content engine, both streaming: a chunk is hashed and unpacked
// piece by piece as it arrives, so no chunk or file is ever held whole in memory.

/// A running checksum of one kind.
struct BCDHasher {
    private var md5: Insecure.MD5?
    private var sha1: Insecure.SHA1?
    private var sha256: SHA256?

    init(_ kind: BCDDigestKind) {
        switch kind {
        case .md5: md5 = Insecure.MD5()
        case .sha1: sha1 = Insecure.SHA1()
        case .sha256: sha256 = SHA256()
        }
    }

    mutating func update(_ bytes: UnsafeRawBufferPointer) {
        guard bytes.count > 0 else { return }
        md5?.update(bufferPointer: bytes)
        sha1?.update(bufferPointer: bytes)
        sha256?.update(bufferPointer: bytes)
    }

    mutating func update(_ data: Data) { data.withUnsafeBytes { update($0) } }

    func finalize() -> String {
        func hex<D: Sequence>(_ digest: D) -> String where D.Element == UInt8 {
            digest.map { String(format: "%02x", $0) }.joined()
        }
        if let md5 { return hex(md5.finalize()) }
        if let sha1 { return hex(sha1.finalize()) }
        return hex(sha256!.finalize())
    }

    static func hex(_ kind: BCDDigestKind, of data: Data) -> String {
        var hasher = BCDHasher(kind)
        hasher.update(data)
        return hasher.finalize()
    }
}

/// A zlib stream (with its header, as GOG sends it) unpacked as it arrives. The output goes to
/// `emit` in pieces of at most `outputSize` bytes; the inflater itself holds zlib's 32 KiB window.
final class BCDInflater {
    private var stream = z_stream()
    private var buffer: [UInt8]
    private(set) var ended = false
    private var open = false

    init(outputSize: Int = 256 << 10) throws {
        buffer = [UInt8](repeating: 0, count: outputSize)
        guard inflateInit_(&stream, ZLIB_VERSION, Int32(MemoryLayout<z_stream>.size)) == Z_OK else { throw BCDContentError.decode }
        open = true
    }

    deinit { if open { inflateEnd(&stream) } }

    /// Feeds compressed bytes. Bytes after the end of the stream are an error.
    func feed(_ input: UnsafeRawBufferPointer, emit: (UnsafeRawBufferPointer) throws -> Void) throws {
        guard input.count > 0 else { return }
        guard !ended, let base = input.baseAddress else { throw BCDContentError.decode }
        stream.next_in = UnsafeMutablePointer(mutating: base.assumingMemoryBound(to: Bytef.self))
        stream.avail_in = uInt(input.count)
        defer { stream.next_in = nil; stream.avail_in = 0 }
        while stream.avail_in > 0 {
            let (status, produced) = buffer.withUnsafeMutableBytes { out -> (Int32, Int) in
                stream.next_out = out.baseAddress!.assumingMemoryBound(to: Bytef.self)
                stream.avail_out = uInt(out.count)
                let status = inflate(&stream, Z_NO_FLUSH)
                return (status, out.count - Int(stream.avail_out))
            }
            if produced > 0 { try buffer.withUnsafeBytes { try emit(UnsafeRawBufferPointer(rebasing: $0[0..<produced])) } }
            switch status {
            case Z_OK:
                continue
            case Z_STREAM_END:
                ended = true
                guard stream.avail_in == 0 else { throw BCDContentError.decode }
                return
            case Z_BUF_ERROR where produced > 0:
                continue
            default:
                throw BCDContentError.decode
            }
        }
        // All input taken; zlib may still hold output for a full buffer.
        while !ended {
            let (status, produced) = buffer.withUnsafeMutableBytes { out -> (Int32, Int) in
                stream.next_out = out.baseAddress!.assumingMemoryBound(to: Bytef.self)
                stream.avail_out = uInt(out.count)
                let status = inflate(&stream, Z_NO_FLUSH)
                return (status, out.count - Int(stream.avail_out))
            }
            if produced > 0 { try buffer.withUnsafeBytes { try emit(UnsafeRawBufferPointer(rebasing: $0[0..<produced])) } }
            if status == Z_STREAM_END { ended = true; break }
            if status == Z_BUF_ERROR || produced == 0 { break }
            guard status == Z_OK else { throw BCDContentError.decode }
        }
    }

    /// The input is complete: the stream must have ended.
    func finish() throws {
        guard ended else { throw BCDContentError.decode }
    }
}

enum BCDZlib {
    /// A whole zlib stream unpacked, refusing more than `limit` bytes of output: for manifests,
    /// never for game data.
    static func inflate(_ data: Data, limit: Int) throws -> Data {
        let inflater = try BCDInflater()
        var out = Data()
        try data.withUnsafeBytes { input in
            try inflater.feed(input) { piece in
                guard out.count + piece.count <= limit else { throw BCDContentError.tooLarge }
                out.append(contentsOf: piece)
            }
        }
        try inflater.finish()
        return out
    }

    /// A store reply that is zlib, or plain JSON (it starts with '{' or '[' then).
    static func jsonData(_ data: Data, limit: Int) throws -> Data {
        guard let first = data.first(where: { $0 != 0x20 && $0 != 0x0A && $0 != 0x0D && $0 != 0x09 }) else {
            throw BCDContentError.decode
        }
        if first == UInt8(ascii: "{") || first == UInt8(ascii: "[") {
            guard data.count <= limit else { throw BCDContentError.tooLarge }
            return data
        }
        return try inflate(data, limit: limit)
    }
}

// MARK: - Chunk files (Epic)

/// An Epic chunk file as it arrives from the network (epic.md §2.3, "Chunk files"): a header, then
/// the chunk's bytes, stored as they are or as one zlib stream. The header says where the body starts
/// and how long it is; everything else it holds (the chunk's GUID, its rolling hash, its checksum, its
/// decoded size) is read and exposed but not trusted: the plan's own checksum of the decoded bytes is
/// the check, so a header field that means something else than assumed cannot fail a good chunk.
///
/// Header, little-endian: magic 0xB1FE3AA2 (u32), header version (u32), header size (u32), body size
/// (u32), GUID (16 bytes), rolling hash (u64), stored-as (u8: bit 0 zlib, bit 1 encrypted); version 2
/// adds a SHA-1 (20 bytes) and a hash type (u8); version 3 the decoded size (u32); version 4 a secret GUID
/// and an authentication tag (32 bytes). Streaming, like BCDInflater: nothing is held but the header.
final class BCDChunkFileDecoder {
    struct Header: Equatable {
        var version: Int
        var headerSize: Int
        var bodySize: Int64
        var guid: [UInt8]
        var rollingHash: UInt64
        var storedAs: UInt8
        var sha1: [UInt8]?
        var decodedSize: Int64?
        var compressed: Bool { storedAs & 1 != 0 }
    }

    static let magic: UInt32 = 0xB1FE_3AA2
    /// What the header of each version holds at least.
    private static let minimumHeader = [1: 41, 2: 62, 3: 66, 4: 98]
    private static let maxHeader = 1024

    private var head: [UInt8] = []
    private var wanted = 12
    private var inflater: BCDInflater?
    private var bodySeen: Int64 = 0
    private(set) var header: Header?
    /// Every byte taken, header included.
    private(set) var received: Int64 = 0

    init() {}

    /// Feeds bytes of the chunk file; the decoded bytes go to `emit` as they come.
    func feed(_ input: UnsafeRawBufferPointer, emit: (UnsafeRawBufferPointer) throws -> Void) throws {
        guard input.count > 0 else { return }
        received += Int64(input.count)
        var rest = input
        while header == nil, rest.count > 0 {
            let take = min(wanted - head.count, rest.count)
            head.append(contentsOf: rest.prefix(take))
            rest = UnsafeRawBufferPointer(rebasing: rest[take...])
            if head.count == 12 && wanted == 12 {
                guard Self.u32(head, 0) == Self.magic else { throw BCDContentError.decode }
                let version = Int(Self.u32(head, 4)), size = Int(Self.u32(head, 8))
                guard let least = Self.minimumHeader[version], size >= least, size <= Self.maxHeader else { throw BCDContentError.decode }
                wanted = size
            }
            if head.count == wanted && wanted > 12 { try parseHeader() }
        }
        guard let header else { return }
        guard rest.count > 0 else { return }
        guard bodySeen + Int64(rest.count) <= header.bodySize else { throw BCDContentError.tooLarge }
        bodySeen += Int64(rest.count)
        if header.compressed {
            try inflater!.feed(rest, emit: emit)
        } else {
            try emit(rest)
        }
    }

    /// The whole file arrived: the header, and a body of exactly the size the header named.
    func finish() throws {
        guard let header, bodySeen == header.bodySize, header.bodySize > 0 else { throw BCDContentError.decode }
        if header.compressed { try inflater!.finish() }
    }

    private func parseHeader() throws {
        let version = Int(Self.u32(head, 4))
        let stored = head[40]
        // Encrypted chunks (preloads) need a key BCD does not have: refused, not retried.
        guard stored & 2 == 0 else {
            throw BCDContentError.refused("This game's files are encrypted by Epic (a pre-release download). BCD can't read them.")
        }
        let body = Int64(Self.u32(head, 12))
        guard body > 0, body <= 1 << 30 else { throw BCDContentError.decode }
        var sha: [UInt8]?
        var decoded: Int64?
        if version >= 2 { sha = Array(head[41..<61]) }
        if version >= 3 { decoded = Int64(Self.u32(head, 62)) }
        header = Header(version: version, headerSize: wanted, bodySize: body, guid: Array(head[16..<32]),
                        rollingHash: Self.u64(head, 32), storedAs: stored, sha1: sha, decodedSize: decoded)
        if stored & 1 != 0 { inflater = try BCDInflater() }
        head = []
    }

    private static func u32(_ bytes: [UInt8], _ at: Int) -> UInt32 {
        (0..<4).reduce(UInt32(0)) { $0 | UInt32(bytes[at + $1]) << (8 * UInt32($1)) }
    }

    private static func u64(_ bytes: [UInt8], _ at: Int) -> UInt64 {
        (0..<8).reduce(UInt64(0)) { $0 | UInt64(bytes[at + $1]) << (8 * UInt64($1)) }
    }
}

