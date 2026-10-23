// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// The container search of docs/research/open-fork-2026-10-18/epic.md §4.9 and §5 M2, done by the app itself:
// after a signed-in start BCD looks through its own logs, the Windows profile, the program data and its own
// folders for anything of that start that must not be left behind (the one-time code, the account's ID, the
// ownership token, or an Epic argument carrying a value instead of a mask) and says how many it found, by a
// count and by the class and name of the file, never by the text. A device test can then show "no code, ID or
// token in the container" from the log alone. It reads, and deletes or changes nothing. Foundation only (the host checks
// compile it).

enum BCDEpicAudit {
    /// One thing looked for.
    struct Needle {
        /// A short word for the log: "code", "id", "token", "arg", "family".
        var label: String
        var bytes: [UInt8]
        /// The needle is UTF-16 (a zero after each character).
        var wide = false
        /// The needle is a marker that a mask may follow ("-AUTH_PASSWORD=*"): only a match followed by something else is a hit.
        var maskedOK: Bool
    }

    /// A place to look: a short class name for the log, the folder, and the folder names (case-insensitive) not entered.
    struct Root {
        var name: String
        var url: URL
        var skip: Set<String> = []
        /// Only files whose name ends with one of these (lower case) are read; empty: every file.
        var suffixes: Set<String> = []
        var maxFiles = 20_000
    }

    struct Hit: Equatable {
        var label: String
        var file: String
    }

    struct Result: Equatable {
        var files = 0
        var bytes: Int64 = 0
        var hits: [Hit] = []
        var counts: [String: Int] = [:]
        /// The search stopped at its time or file limit.
        var truncated = false
        var milliseconds = 0

        var total: Int { counts.values.reduce(0, +) }
        /// "files=N bytes=M hits=H code=a id=b token=c arg=d family=e ms=T", numbers only.
        var summary: String {
            "files=\(files) bytes=\(bytes) hits=\(total) " + ["code", "id", "token", "arg", "family"].map { "\($0)=\(counts[$0] ?? 0)" }.joined(separator: " ")
                + " truncated=\(truncated ? 1 : 0) ms=\(milliseconds)"
        }
    }

    /// The markers always searched, and the exact values of this start (the code, the account's ID, the token's first characters), each
    /// as ASCII and as UTF-16 (Windows programs often log in that).
    static func needles(code: String?, accountID: String?, token: String?) -> [Needle] {
        var list: [Needle] = []
        func add(_ label: String, _ text: String, masked: Bool) {
            guard !text.isEmpty else { return }
            let ascii = Array(text.utf8)
            list.append(Needle(label: label, bytes: ascii, wide: false, maskedOK: masked))
            list.append(Needle(label: label, bytes: ascii.flatMap { [$0, 0] }, wide: true, maskedOK: masked))
        }
        add("family", "egoc1~", masked: true)
        add("family", "eg1~", masked: true)
        add("arg", "-AUTH_PASSWORD=", masked: true)
        add("arg", "-epicuserid=", masked: true)
        add("arg", "-epicusername=", masked: true)
        if let code, code.utf8.count >= 16 { add("code", code, masked: false) }
        if let accountID, accountID.utf8.count >= 16 { add("id", accountID, masked: false) }
        if let token, token.utf8.count >= 16 { add("token", String(token.prefix(64)), masked: false) }
        return list
    }

    /// What may follow a marker for it to be a mask and not a value: '*', '<' (a placeholder), a space, a quote or a line end.
    private static func followedByMask(_ raw: UnsafeRawBufferPointer, at next: Int, wide: Bool, atEnd: Bool) -> Bool? {
        guard next < raw.count else { return atEnd ? true : nil }       // the file's end: nothing follows; else look again with more bytes
        if wide {
            guard next + 1 < raw.count else { return atEnd ? false : nil }
            guard raw[next + 1] == 0 else { return false }
        }
        let c = raw[next]
        return c == 0x2A || c == 0x3C || c == 0x20 || c == 0x22 || c == 0x27 || c == 0x0A || c == 0x0D
    }

    /// Looks for `needles` in one file's bytes read from `handle` (a megabyte at a time, with an overlap so a match across two reads is
    /// found). Returns the labels found at least once.
    static func search(_ handle: FileHandle, needles: [Needle]) -> Set<String> {
        guard let longest = needles.map({ $0.bytes.count }).max() else { return [] }
        var found = Set<String>()
        var carry = Data()
        let chunk = 1 << 20
        while true {
            guard let piece = try? handle.read(upToCount: chunk), !piece.isEmpty else { break }
            var window = carry
            window.append(piece)
            let atEnd = piece.count < chunk
            window.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                guard let base = raw.baseAddress else { return }
                for needle in needles where !found.contains(needle.label) {
                    var from = 0
                    while from + needle.bytes.count <= raw.count {
                        let match: UnsafeMutableRawPointer? = needle.bytes.withUnsafeBytes { memmem(base + from, raw.count - from, $0.baseAddress!, needle.bytes.count) }
                        guard let match else { break }
                        let at = base.distance(to: UnsafeRawPointer(match))
                        var hit = true
                        if needle.maskedOK {
                            // nil: more bytes are needed to tell (the next read's window starts early enough to see this match again).
                            if let masked = followedByMask(raw, at: at + needle.bytes.count, wide: needle.wide, atEnd: atEnd) { hit = !masked } else { hit = false }
                        }
                        if hit { found.insert(needle.label); break }
                        from = at + 1
                    }
                }
            }
            if atEnd { break }
            carry = window.suffix(longest + 2)
        }
        return found
    }

    /// Looks through the roots. At most `seconds` and each root's file limit; files over `maxFileBytes` are skipped; links are never followed.
    static func run(roots: [Root], needles: [Needle], seconds: TimeInterval = 20, maxFileBytes: Int64 = 400 << 20, maxHits: Int = 8) -> Result {
        var result = Result()
        let started = Date()
        let fm = FileManager.default
        for root in roots {
            guard let walker = fm.enumerator(at: root.url, includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey, .isDirectoryKey, .fileSizeKey],
                                             options: [], errorHandler: { _, _ in true }) else { continue }
            var seen = 0
            for case let url as URL in walker {
                if Date().timeIntervalSince(started) > seconds { result.truncated = true; break }
                guard let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .isDirectoryKey, .fileSizeKey]) else { continue }
                if values.isSymbolicLink == true { walker.skipDescendants(); continue }
                if values.isDirectory == true {
                    if root.skip.contains(url.lastPathComponent.lowercased()) { walker.skipDescendants() }
                    continue
                }
                guard values.isRegularFile == true, let size = values.fileSize, Int64(size) <= maxFileBytes else { continue }
                if !root.suffixes.isEmpty, !root.suffixes.contains(where: { url.lastPathComponent.lowercased().hasSuffix($0) }) { continue }
                seen += 1
                if seen > root.maxFiles { result.truncated = true; break }
                guard let handle = try? FileHandle(forReadingFrom: url) else { continue }
                result.files += 1
                result.bytes += Int64(size)
                let labels = search(handle, needles: needles)
                try? handle.close()
                for label in labels.sorted() {
                    result.counts[label, default: 0] += 1
                    if result.hits.count < maxHits {
                        // The enumerator may hand back the folder under another spelling of its path (/private/var for /var).
                        let shown = url.resolvingSymlinksInPath().path, base = root.url.resolvingSymlinksInPath().path
                        let relative = shown.hasPrefix(base) ? String(shown.dropFirst(base.count)) : url.lastPathComponent
                        result.hits.append(Hit(label: label, file: root.name + ":" + relativeForLog(relative)))
                    }
                }
            }
        }
        result.milliseconds = Int(Date().timeIntervalSince(started) * 1000)
        return result
    }

    /// A file's place for the log: its path below the root, kept to letters, digits and a few marks, at most 90 characters (the user's folder
    /// name inside a Windows profile is replaced).
    static func relativeForLog(_ path: String) -> String {
        let allowed = Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-+()/")
        var text = String(path.filter { allowed.contains($0) })
        if let range = text.range(of: #"(?i)/users/[^/]+"#, options: .regularExpression) { text.replaceSubrange(range, with: "/users/<user>") }
        return String(text.prefix(90))
    }
}
