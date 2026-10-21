// SPDX-License-Identifier: GPL-3.0-or-later
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
//
// Wine Mono, the .NET Framework runtime Wine's mscoree loads, downloaded from WineHQ
// on first use (setup's Wine Mono page, or Settings › .NET Framework). Release builds
// do not carry it (THIRD-PARTY-NOTICES.md); a development build made after
// build/wine-mono/fetch.sh has it in the bundle instead, and then nothing is downloaded.
//
// The download is the x86 tarball build/wine-mono/pin.sh pins, checked against the
// same SHA-256. It is unpacked here, on the device, without the compile-time reference
// assemblies (lib/mono/*-api, as build/wine-mono/bundle.sh leaves them out), and the
// unpacked mscorlib.dll gets bundle.sh's ml1281 patch, checked against both of its
// pinned hashes. The result goes to Library/Application Support/WineMono/wine-mono,
// kept out of backups; WineProcessBridge.m links C:\windows\mono\mono-2.0 to it every
// session when the bundle has none. Madeira does not redistribute Wine Mono.
// Log tag: [wine-mono].

import Foundation
import Compression
import CryptoKit
import SwiftUI

// MARK: - Pin and installer (Foundation, Compression and CryptoKit only; tests/host/check-wine-mono.py compiles this part)

/// What build/wine-mono/pin.sh pins (check-wine-mono.py holds the two to the same values).
enum WineMonoPin {
    static let version = "11.3.0"
    static let url = URL(string: "https://dl.winehq.org/wine/wine-mono/11.3.0/wine-mono-11.3.0-x86.tar.xz")!
    static let tarSHA256 = "54a1b0111c3fe4b785eae688af94d27e64995707ad648b6fee8000381b80d298"
    /// The tarball's size and its unpacked tar stream's, for progress.
    static let tarBytes: Int64 = 39_412_620
    static let tarStreamBytes: Int64 = 231_864_320
    /// lib/mono/4.5/mscorlib.dll before and after the ml1281 patch.
    static let mscorlibSHA256 = "b715602dfd62e0eec01c8f2167fb6afee859ea7e6513eed2c7b8aeafc6da33d9"
    static let mscorlibPatchedSHA256 = "5affb26c82c6499d46074d904f130a1a1c8de8b774372ebdac6fc03720b10105"
    /// ml1281 (bundle.sh): GC.Collect(int, GCCollectionMode, bool, bool)'s `mode == Optimized`
    /// branch (ldloc.0; ldc.i4.4; or; stloc.0 at IL_003c) becomes ret; nop; nop; nop.
    static let patchOffset = 0x53BA4
    static let patchContext: [UInt8] = [0x16, 0x0A, 0x03, 0x18, 0x33, 0x04, 0x06, 0x1A, 0x60, 0x0A,
                                        0x05, 0x2C, 0x04, 0x06, 0x1E, 0x60, 0x0A, 0x04, 0x2C, 0x06]   // offset - 6 ..< offset + 14
    static let patchBytes: [UInt8] = [0x2A, 0x00, 0x00, 0x00]
}

enum WineMonoInstallError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}

/// Unpacks and patches; no network, UI or app state, so the host check runs it on the
/// real tarball.
enum WineMonoInstaller {
    static func sha256(of file: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: file)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1 << 20), !chunk.isEmpty { hasher.update(data: chunk) }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    /// The path under the runtime folder a tar entry unpacks to, or nil to leave it out:
    /// the leading wine-mono-<version>/ is dropped, the lib/mono/*-api reference
    /// assemblies are skipped, and nothing may leave the folder.
    static func relativePath(_ entry: String) -> String? {
        var parts = entry.split(separator: "/", omittingEmptySubsequences: true).map(String.init)
        guard !entry.hasPrefix("/"), !parts.contains(".."), !parts.contains("."), parts.count >= 1 else { return nil }
        parts.removeFirst()                                     // wine-mono-<version>
        guard !parts.isEmpty else { return nil }
        if parts.count >= 3, parts[0] == "lib", parts[1] == "mono", parts[2].hasSuffix("-api") { return nil }
        return parts.joined(separator: "/")
    }

    /// Unpacks a GNU or POSIX tar compressed with xz (Apple's LZMA decoder reads the .xz
    /// container) into `destination`: directories and regular files only, long names
    /// through GNU 'L' entries or pax headers. `progress` gets the tar bytes read so far.
    /// Returns the number of files written.
    @discardableResult
    static func unpack(tarXZ: URL, into destination: URL, progress: (Int64) -> Void = { _ in }) throws -> Int {
        let fm = FileManager.default
        try fm.createDirectory(at: destination, withIntermediateDirectories: true)
        let input = try FileHandle(forReadingFrom: tarXZ)
        defer { try? input.close() }

        var stream = compression_stream(dst_ptr: UnsafeMutablePointer<UInt8>(bitPattern: 1)!, dst_size: 0,
                                        src_ptr: UnsafePointer<UInt8>(bitPattern: 1)!, src_size: 0, state: nil)
        guard compression_stream_init(&stream, COMPRESSION_STREAM_DECODE, COMPRESSION_LZMA) == COMPRESSION_STATUS_OK else {
            throw WineMonoInstallError.message("BCD couldn't start the xz decoder.")
        }
        defer { compression_stream_destroy(&stream) }

        let inCap = 1 << 20, outCap = 1 << 20
        let inBuf = UnsafeMutablePointer<UInt8>.allocate(capacity: inCap)
        let outBuf = UnsafeMutablePointer<UInt8>.allocate(capacity: outCap)
        defer { inBuf.deallocate(); outBuf.deallocate() }

        var tar = TarReader(destination: destination)
        var inputDone = false, finished = false, read: Int64 = 0
        while !finished {
            if stream.src_size == 0 && !inputDone {
                let chunk = try input.read(upToCount: inCap) ?? Data()
                if chunk.isEmpty { inputDone = true } else {
                    chunk.copyBytes(to: inBuf, count: chunk.count)
                    stream.src_ptr = UnsafePointer(inBuf)
                    stream.src_size = chunk.count
                }
            }
            stream.dst_ptr = outBuf
            stream.dst_size = outCap
            let status = compression_stream_process(&stream, inputDone ? Int32(COMPRESSION_STREAM_FINALIZE.rawValue) : 0)
            let produced = outCap - stream.dst_size
            if produced > 0 {
                try tar.consume(UnsafeBufferPointer(start: outBuf, count: produced))
                read += Int64(produced)
                progress(read)
            }
            switch status {
            case COMPRESSION_STATUS_END: finished = true
            case COMPRESSION_STATUS_OK:
                if inputDone && produced == 0 && stream.src_size == 0 {
                    throw WineMonoInstallError.message("The download is incomplete.")
                }
            default: throw WineMonoInstallError.message("The downloaded file isn't an xz archive BCD can read.")
            }
        }
        try tar.finish()
        return tar.files
    }

    /// bundle.sh's ml1281 patch of lib/mono/4.5/mscorlib.dll under `runtime`, checked
    /// against both pinned hashes; an already patched file is left as it is.
    static func patchMscorlib(in runtime: URL) throws {
        let file = runtime.appendingPathComponent("lib/mono/4.5/mscorlib.dll")
        var data = try Data(contentsOf: file)
        let digest = { (d: Data) in SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined() }
        let before = digest(data)
        if before == WineMonoPin.mscorlibPatchedSHA256 { return }
        guard before == WineMonoPin.mscorlibSHA256 else {
            throw WineMonoInstallError.message("mscorlib.dll doesn't match the pinned build.")
        }
        let off = WineMonoPin.patchOffset
        guard data.count >= off + 14, Array(data[(off - 6)..<(off + 14)]) == WineMonoPin.patchContext else {
            throw WineMonoInstallError.message("mscorlib.dll doesn't have the bytes BCD expects to patch.")
        }
        data.replaceSubrange(off..<(off + 4), with: WineMonoPin.patchBytes)
        guard digest(data) == WineMonoPin.mscorlibPatchedSHA256 else {
            throw WineMonoInstallError.message("After patching, mscorlib.dll doesn't match the pinned result.")
        }
        try data.write(to: file, options: .atomic)
    }
}

/// A streaming tar reader: header blocks, GNU long names, pax paths, file data.
private struct TarReader {
    let destination: URL
    private(set) var files = 0
    private var header = [UInt8]()
    private var remaining: Int64 = 0         // data bytes of the current entry still to come
    private var padding: Int64 = 0           // then the zero fill up to 512
    private var sink: FileHandle?
    private var collecting: UInt8 = 0        // 'L' or 'x': this entry's data is metadata
    private var collected = [UInt8]()
    private var nextName: String?
    private var zeroBlocks = 0

    init(destination: URL) { self.destination = destination }

    mutating func consume(_ bytes: UnsafeBufferPointer<UInt8>) throws {
        var i = 0
        while i < bytes.count {
            if zeroBlocks >= 2 { return }                       // the end-of-archive marker
            if remaining > 0 {
                let n = Int(min(remaining, Int64(bytes.count - i)))
                let slice = UnsafeBufferPointer(rebasing: bytes[i..<(i + n)])
                if let sink { try sink.write(contentsOf: Data(slice)) }
                else if collecting != 0 { collected.append(contentsOf: slice) }
                remaining -= Int64(n); i += n
                if remaining == 0 { try endData() }
                continue
            }
            if padding > 0 {
                let n = Int(min(padding, Int64(bytes.count - i)))
                padding -= Int64(n); i += n
                continue
            }
            let n = min(512 - header.count, bytes.count - i)
            header.append(contentsOf: bytes[i..<(i + n)]); i += n
            if header.count == 512 { try beginEntry(); header.removeAll(keepingCapacity: true) }
        }
    }

    mutating func finish() throws {
        guard remaining == 0, header.isEmpty || header.allSatisfy({ $0 == 0 }) else {
            throw WineMonoInstallError.message("The archive stops partway through a file.")
        }
        try sink?.close(); sink = nil
    }

    private func field(_ start: Int, _ length: Int) -> String {
        let raw = header[start..<(start + length)].prefix { $0 != 0 }
        return String(decoding: raw, as: UTF8.self)
    }

    private func octal(_ start: Int, _ length: Int) throws -> Int64 {
        let text = field(start, length).trimmingCharacters(in: CharacterSet(charactersIn: " \0"))
        if text.isEmpty { return 0 }
        guard let value = Int64(text, radix: 8) else { throw WineMonoInstallError.message("One of the archive's headers is damaged.") }
        return value
    }

    private mutating func beginEntry() throws {
        if header.allSatisfy({ $0 == 0 }) { zeroBlocks += 1; return }
        zeroBlocks = 0
        let size = try octal(124, 12)
        let type = header[156]
        let magic = Array(header[257..<263])
        var name = field(0, 100)
        if magic == Array("ustar\0".utf8) {                    // POSIX: a prefix may hold the start of the path
            let prefix = field(345, 155)
            if !prefix.isEmpty { name = prefix + "/" + name }
        }
        if let long = nextName { name = long; nextName = nil }
        remaining = size
        padding = (512 - size % 512) % 512
        collecting = 0
        switch type {
        case UInt8(ascii: "L"), UInt8(ascii: "x"):
            collecting = type; collected.removeAll(keepingCapacity: true)
        case UInt8(ascii: "g"):
            break                                               // a global pax header: nothing needed here
        case UInt8(ascii: "5"):
            if let path = WineMonoInstaller.relativePath(name) {
                try FileManager.default.createDirectory(at: destination.appendingPathComponent(path, isDirectory: true),
                                                        withIntermediateDirectories: true)
            }
        case UInt8(ascii: "0"), 0, UInt8(ascii: "7"):
            if let path = WineMonoInstaller.relativePath(name) {
                let url = destination.appendingPathComponent(path)
                try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
                guard FileManager.default.createFile(atPath: url.path, contents: nil) else {
                    throw WineMonoInstallError.message("BCD couldn't write \(path).")
                }
                sink = try FileHandle(forWritingTo: url)
                files += 1
            }
        default:
            break                                               // links and devices: none in Wine Mono's tarball
        }
        if remaining == 0 { try endData() }
    }

    private mutating func endData() throws {
        if let sink { try sink.close(); self.sink = nil }
        if collecting == UInt8(ascii: "L") {
            nextName = String(decoding: collected.prefix { $0 != 0 }, as: UTF8.self)
        } else if collecting == UInt8(ascii: "x") {
            // pax records: "<length> key=value\n"
            for record in String(decoding: collected, as: UTF8.self).split(separator: "\n") {
                guard let space = record.firstIndex(of: " ") else { continue }
                let pair = record[record.index(after: space)...]
                if pair.hasPrefix("path=") { nextName = String(pair.dropFirst(5)) }
            }
        }
        collecting = 0
    }
}

// MARK: - Model

/// Wine Mono on this device: in the bundle (a development build), downloaded, or not here.
@MainActor final class WineMonoModel: ObservableObject {
    static let shared = WineMonoModel()

    enum Phase: Equatable {
        case idle
        case downloading(Double)                 // fraction of the download
        case installing(Double)                  // fraction of the archive unpacked
    }

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var installed = false
    @Published var error: String?
    private var observation: NSKeyValueObservation?

    /// Library/Application Support/WineMono; WineProcessBridge.m looks for `wine-mono` in it.
    nonisolated static var root: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("WineMono", isDirectory: true)
    }
    nonisolated static var runtime: URL { root.appendingPathComponent("wine-mono", isDirectory: true) }
    /// A development build made after build/wine-mono/fetch.sh carries it (bundle.sh).
    nonisolated static var bundled: Bool {
        FileManager.default.fileExists(atPath: Bundle.main.bundleURL.appendingPathComponent("wine-mono/bin/libmono-2.0-x86.dll").path)
    }
    nonisolated static var downloaded: Bool {
        FileManager.default.fileExists(atPath: runtime.appendingPathComponent("bin/libmono-2.0-x86.dll").path)
    }
    /// Either way, .NET Framework programs can start.
    nonisolated static var available: Bool { bundled || downloaded }

    var busy: Bool { phase != .idle }

    private init() { refresh() }

    func refresh() { installed = Self.downloaded }

    var status: String {
        switch phase {
        case .downloading(let f):
            let total = Double(WineMonoPin.tarBytes) / 1_000_000
            return "\(Int((f * total).rounded())) of \(Int(total.rounded())) MB downloaded…"
        case .installing(let f): return "\(Int((f * 100).rounded()))% installed…"
        case .idle:
            if Self.bundled { return "Built into this copy of BCD" }
            return installed ? "Version \(WineMonoPin.version) installed" : "Not downloaded yet"
        }
    }

    private func log(_ line: String) { LogStore.shared.log("[wine-mono] " + line) }

    func install() {
        guard !busy, !Self.available else { return }
        error = nil
        let root = Self.root
        do {
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
            var values = URLResourceValues(); values.isExcludedFromBackup = true
            var url = root; try url.setResourceValues(values)
            let free = try root.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]).volumeAvailableCapacityForImportantUsage ?? 0
            guard free > 450_000_000 else {
                error = "Installing Wine Mono takes about 450 MB of free space; once installed it uses about 130 MB."
                return
            }
        } catch {
            fail("BCD couldn't set up the Wine Mono folder: \(error.localizedDescription)"); return
        }
        log("download \(WineMonoPin.url.absoluteString)")
        phase = .downloading(0)
        let archive = root.appendingPathComponent("download.tar.xz")
        let task = URLSession.shared.downloadTask(with: WineMonoPin.url) { temp, response, failure in
            // The temporary file is removed when this returns: move it first.
            var moved: Error? = failure
            if failure == nil, let temp {
                if let http = response as? HTTPURLResponse, http.statusCode != 200 {
                    moved = WineMonoInstallError.message("WineHQ replied with HTTP status \(http.statusCode).")
                } else {
                    try? FileManager.default.removeItem(at: archive)
                    do { try FileManager.default.moveItem(at: temp, to: archive) } catch { moved = error }
                }
            }
            let outcome = moved
            Task { @MainActor in WineMonoModel.shared.downloaded(archive: archive, error: outcome) }
        }
        observation = task.progress.observe(\.fractionCompleted) { progress, _ in
            let f = progress.fractionCompleted
            Task { @MainActor in
                let model = WineMonoModel.shared
                if case .downloading = model.phase { model.phase = .downloading(f) }
            }
        }
        task.resume()
    }

    private func downloaded(archive: URL, error failure: Error?) {
        observation = nil
        if let failure {
            fail("The download couldn't finish: \(failure.localizedDescription)"); return
        }
        phase = .installing(0)
        let root = Self.root, runtime = Self.runtime
        Task.detached(priority: .userInitiated) {
            let staging = root.appendingPathComponent("wine-mono.partial", isDirectory: true)
            let fm = FileManager.default
            do {
                guard try WineMonoInstaller.sha256(of: archive) == WineMonoPin.tarSHA256 else {
                    throw WineMonoInstallError.message("The downloaded file isn't the pinned Wine Mono \(WineMonoPin.version).")
                }
                try? fm.removeItem(at: staging)
                var last = -1
                let files = try WineMonoInstaller.unpack(tarXZ: archive, into: staging) { read in
                    let percent = Int(read * 100 / WineMonoPin.tarStreamBytes)
                    guard percent != last else { return }
                    last = percent
                    Task { @MainActor in WineMonoModel.shared.phase = .installing(Double(percent) / 100) }
                }
                try WineMonoInstaller.patchMscorlib(in: staging)
                try Data((WineMonoPin.version + "\n").utf8).write(to: staging.appendingPathComponent("MADEIRA-VERSION"))
                try? fm.removeItem(at: runtime)
                try fm.moveItem(at: staging, to: runtime)
                try? fm.removeItem(at: archive)
                await MainActor.run { WineMonoModel.shared.installedNow(files: files) }
            } catch {
                try? fm.removeItem(at: staging)
                try? fm.removeItem(at: archive)
                let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
                await MainActor.run { WineMonoModel.shared.fail("Wine Mono couldn't be installed: \(message)") }
            }
        }
    }

    private func installedNow(files: Int) {
        phase = .idle
        refresh()
        log("installed \(WineMonoPin.version) files=\(files)")
    }

    private func fail(_ message: String) {
        phase = .idle
        error = message
        log("failed: \(message)")
    }

    /// Settings › .NET Framework › Remove. Not while a session runs: it may be using it.
    func remove() {
        guard !busy, wine_process_is_running() == 0 else { return }
        try? FileManager.default.removeItem(at: Self.runtime)
        refresh()
        log("removed")
    }
}
