// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

/// Epic's log lines: [epic-auth] and [epic-library] (counts, HTTP statuses, timings, short reasons).
/// Never a token, a code, a cookie, an account ID in full, a name or an e-mail address: every line is
/// masked here before it goes to the app's log (BCDContentLog, the same sink GOG's lines use), on top
/// of the content engine's own pass over OAuth-shaped key/value pairs. tests/host/check-epic.py also
/// reads every call site for what its interpolations may carry. Foundation only.
enum BCDEpicLog {
    static func write(_ line: String) { BCDContentLog.write(redact(line)) }

    /// The masks, in order: Epic's two token families, the keys Epic's replies carry secrets and names
    /// under, Epic's start arguments, e-mail addresses, and any 32-digit hexadecimal run (account IDs, authorization codes and
    /// the launcher client's refresh tokens all have that shape).
    static let rules: [(pattern: String, template: String)] = [
        (#"eg1~[A-Za-z0-9._~+/=-]*"#, "eg1~<redacted>"),
        (#"egoc1~[A-Za-z0-9._~+/=-]*"#, "egoc1~<redacted>"),
        (#"(?i)(\b(?:authorizationCode|exchange_?code|display_?name|account_?id|user_?name|sid)["']?\s*[=:]\s*["']?)[^&\s"',}]+"#, "$1<redacted>"),
        // Epic's start arguments (milestone 2): no log line of BCD's carries them; masked all the same.
        (#"(?i)(-AUTH_PASSWORD=|-epicuserid=|-epicusername=)(?:"[^"]*"|[^\s"']+)"#, "$1<redacted>"),
        (#"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"#, "<email>"),
        (#"\b[0-9A-Fa-f]{32}\b"#, "<id32>"),
    ]

    static func redact(_ text: String) -> String {
        var out = text
        for rule in rules {
            out = out.replacingOccurrences(of: rule.pattern, with: rule.template, options: .regularExpression)
        }
        return out
    }

    /// An account ID shown as its first and last two characters ("3a..f1"): enough to tell two
    /// accounts apart in a log, not enough to find one.
    static func mask(_ id: String) -> String {
        guard id.count >= 8 else { return ".." }
        return String(id.prefix(2)) + ".." + String(id.suffix(2))
    }

    /// A game's app name for a log line: the name itself when it is a plain word (Epic's codenames), and masked like
    /// an account ID when it is a 32-digit identifier (which the masks above would blank).
    static func app(_ name: String) -> String {
        guard !name.isEmpty, name.utf8.count <= 40, name.utf8.allSatisfy({ ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x41 && $0 <= 0x5A) || ($0 >= 0x61 && $0 <= 0x7A) || $0 == 0x5F || $0 == 0x2D || $0 == 0x2E }) else { return ".." }
        return name.utf8.count == 32 && name.utf8.allSatisfy({ ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x41 && $0 <= 0x46) || ($0 >= 0x61 && $0 <= 0x66) }) ? mask(name) : name
    }

    /// HTTP statuses with their counts, "200x12,404x1" ("none" for no call).
    static func statuses(_ counts: [Int: Int]) -> String {
        counts.isEmpty ? "none" : counts.keys.sorted().map { "\($0)x\(counts[$0]!)" }.joined(separator: ",")
    }

    /// What a reply looks like, for the log when BCD cannot read it: its keys with the kind of each value
    /// (s string, n number, b bool, N null, a array, o object), for each list its length and its first entry's
    /// keys the same way, and for a dictionary of entries the first entry's keys. Never a value. Keys that are
    /// IDs (32 hexadecimal digits: the catalog's) are counted, not named.
    static func shape(_ object: [String: Any]) -> String {
        func kind(_ value: Any) -> String {
            if value is NSNull { return "N" }
            if value is String { return "s" }
            if let number = value as? NSNumber { return CFGetTypeID(number) == CFBooleanGetTypeID() ? "b" : "n" }
            if value is [Any] { return "a" }
            return value is [String: Any] ? "o" : "?"
        }
        func named(_ key: String) -> Bool { key.utf8.count <= 40 && key.utf8.allSatisfy { ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x41 && $0 <= 0x5A) || ($0 >= 0x61 && $0 <= 0x7A) || $0 == 0x5F } }
        func keys(_ item: [String: Any]) -> String {
            let ids = item.keys.filter { BCDEpicAuth.isBareCode($0) }.count
            let list = item.keys.filter { named($0) && !BCDEpicAuth.isBareCode($0) }.sorted().prefix(40).map { $0 + "=" + kind(item[$0]!) }
            return (list + (ids > 0 ? ["ids=\(ids)"] : [])).joined(separator: ",")
        }
        var text = "top[" + keys(object) + "]"
        for name in object.keys.filter(named).sorted().prefix(8) {
            guard let list = object[name] as? [Any] else { continue }
            text += " " + name + "=\(list.count)"
            if let first = list.first as? [String: Any] { text += " first[" + keys(first) + "]" }
        }
        if let first = object.sorted(by: { $0.key < $1.key }).compactMap({ $0.value as? [String: Any] }).first { text += " entry[" + keys(first) + "]" }
        return text
    }

    static func now() -> UInt64 { DispatchTime.now().uptimeNanoseconds }

    static func millis(since start: UInt64) -> Int { Int((now() &- start) / 1_000_000) }

    /// A short, secret-free reason for the log.
    static func reason(_ error: Error) -> String {
        if let error = error as? BCDEpicError { return error.logReason }
        if let error = error as? URLError { return "net-\(error.code.rawValue)" }
        if let error = error as? BCDLogReasonError { return error.logReason }
        return "other"
    }
}
