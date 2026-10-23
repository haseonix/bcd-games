// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// The Swift half of tests/host/check-epic.py: BCDEpic's Foundation-only files (BCDEpicLog, BCDEpicAuth,
// BCDEpicSession, BCDEpicCatalog) and the content engine's secret store and log, compiled on the host
// and run against a stand-in for Epic written here. Epic is never contacted; every reply is synthetic
// and every name, code and token below is made up.

import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

var failures = 0
func check(_ ok: Bool, _ what: String) {
    print((ok ? "PASS: " : "FAIL: ") + what)
    if !ok { failures += 1 }
    fflush(stdout)
}

func expectError(_ what: String, _ expected: BCDEpicError? = nil, _ body: () async throws -> Void) async {
    do {
        try await body()
        check(false, what + " (no error)")
    } catch {
        if let expected { check((error as? BCDEpicError) == expected, what + " (\(error))") } else { check(true, what) }
    }
}

let logLock = NSLock()
nonisolated(unsafe) var logs: [String] = []
func installSink() {
    BCDContentLog.sink = { line in
        logLock.lock(); logs.append(line); logLock.unlock()
        print("LOG " + line)
    }
}

final class Clock: @unchecked Sendable {
    private let lock = NSLock()
    private var value = Date(timeIntervalSince1970: 1_800_000_000)
    var now: Date { lock.lock(); defer { lock.unlock() }; return value }
    func advance(_ seconds: TimeInterval) { lock.lock(); value = value.addingTimeInterval(seconds); lock.unlock() }
}

// Made-up secrets: none may reach a log or the cache.
let GOODCODE = "0123456789abcdef0123456789abcdef"
let ACCOUNT = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
let NAME = "SecretDisplayName"
let ACCESS1 = "eg1~ACCESS1SECRET.PAYLOAD1.SIGNATURE1"
let ACCESS2 = "eg1~ACCESS2SECRET.PAYLOAD2.SIGNATURE2"
let ACCESS3 = "eg1~ACCESS3SECRET.PAYLOAD3.SIGNATURE3"
let REFRESH1 = "feedfacefeedfacefeedfacefeedface"
let REFRESH2 = "cafebabecafebabecafebabecafebabe"
let REFRESH3 = "deadbeefdeadbeefdeadbeefdeadbeef"
let SECRETS = [GOODCODE, ACCOUNT, NAME, "ACCESS1SECRET", "ACCESS2SECRET", "ACCESS3SECRET", REFRESH1, REFRESH2, REFRESH3, "SIGNATURE1"]

func json(_ object: Any) -> Data { try! JSONSerialization.data(withJSONObject: object) }
func object(_ data: Data) -> [String: Any] { (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:] }

func formFields(_ text: String) -> [String: String] {
    var out: [String: String] = [:]
    for pair in text.split(separator: "&") {
        let parts = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
        out[String(parts[0]).removingPercentEncoding ?? ""] = parts.count > 1 ? String(parts[1]).removingPercentEncoding ?? "" : ""
    }
    return out
}

// MARK: Epic's side

struct Rec {
    var app: String
    var ns: String
    var cat: String
    var sandbox = "PUBLIC"
}

func catID(_ n: Int) -> String { String(format: "%032x", n) }

/// The account's records, in the order the library service lists them.
func makeOwned() -> [Rec] {
    var list: [Rec] = [
        Rec(app: "Alpha", ns: "nsa", cat: catID(1)),
        Rec(app: "Beta", ns: "nsb", cat: catID(2)),
        Rec(app: "AlphaDLC", ns: "nsa", cat: catID(3)),
        Rec(app: "ModPack", ns: "nsm", cat: catID(4)),
        Rec(app: "UEAsset", ns: "ue", cat: catID(5)),
        Rec(app: "SecretSandbox", ns: "nsp", cat: catID(6), sandbox: "PRIVATE"),
        Rec(app: "MacOnly", ns: "nsx", cat: catID(7)),
        Rec(app: "bad name", ns: "nsy", cat: catID(8)),
        Rec(app: "Alpha", ns: "nsa", cat: catID(1)),
        Rec(app: "Extra", ns: "nse", cat: catID(9)),
        Rec(app: "Third", ns: "nst", cat: catID(10)),
        Rec(app: "NoInfo", ns: "nsn", cat: catID(11)),
        Rec(app: "Flaky", ns: "nsf", cat: catID(12)),
        Rec(app: "Generic", ns: "nsg", cat: catID(13)),
    ]
    // Thirty more games, each in its own namespace (pages, and four catalog calls at a time).
    for i in 0..<30 { list.append(Rec(app: "G\(i)", ns: "nsgame\(i)", cat: catID(100 + i))) }
    // Five items of one namespace (one bulk call, two when the batch is small).
    for i in 0..<5 { list.append(Rec(app: "Pack\(i)", ns: "nspack", cat: catID(200 + i))) }
    return list
}

func image(_ type: String, _ url: String, _ w: Int, _ h: Int) -> [String: Any] { ["type": type, "url": url, "width": w, "height": h] }

func entry(for rec: Rec) -> [String: Any]? {
    var e: [String: Any] = ["id": rec.cat, "namespace": rec.ns, "title": "Title of " + rec.app,
                            "categories": [["path": "games"], ["path": "applications"]],
                            "releaseInfo": [["appId": rec.app, "platform": ["Windows"]]],
                            "keyImages": [image("DieselGameBoxTall", "https://cdn1.epicgames.com/\(rec.app)_tall.jpg", 1200, 1600),
                                          image("DieselGameBox", "https://cdn1.epicgames.com/\(rec.app)_wide.jpg", 2560, 1440),
                                          image("Thumbnail", "https://cdn1.epicgames.com/\(rec.app)_thumb.jpg", 200, 200)]]
    switch rec.app {
    case "Alpha":
        e["customAttributes"] = ["FolderName": ["type": "STRING", "value": "AlphaFolder"], "CanRunOffline": ["type": "STRING", "value": "true"],
                                 "OwnershipToken": ["type": "STRING", "value": "false"]]
    case "Beta":
        e["keyImages"] = [image("OfferImageTall", "http://cdn2.unrealengine.com/beta_tall.jpg", 800, 1000),
                          image("OfferImageWide", "http://cdn2.unrealengine.com/beta_wide.jpg", 1920, 1080)]
        e["releaseInfo"] = [["appId": "Beta", "platform": ["Windows", "Mac"]]]
        e["customAttributes"] = ["UseAccessControl": ["type": "STRING", "value": "true"], "OwnershipToken": ["value": "True"]]
    case "AlphaDLC":
        e["mainGameItem"] = ["id": catID(1), "title": "Title of Alpha"]
    case "ModPack":
        e["categories"] = [["path": "mods"]]
    case "MacOnly":
        e["releaseInfo"] = [["appId": "MacOnly", "platform": ["Mac"]]]
    case "Extra":
        e["categories"] = [["path": "addons/durable"]]
    case "Third":
        e["customAttributes"] = ["ThirdPartyManagedProvider": ["type": "STRING", "value": "Ubisoft"]]
    case "Generic":
        e["keyImages"] = [image("Foo", "https://cdn1.epicgames.com/g_tall.jpg", 800, 1200), image("Bar", "https://cdn1.epicgames.com/g_wide.jpg", 1600, 900)]
        e.removeValue(forKey: "releaseInfo")
    case "NoInfo":
        return nil
    default:
        break
    }
    return e
}

final class StubEpic: BCDEpicTransport, @unchecked Sendable {
    struct Call { var method: String; var host: String; var path: String; var query: String; var auth: String; var body: String }
    let lock = NSLock()
    var calls: [Call] = []
    var refreshes = 0
    var validAccess: Set<String> = []
    var owned = makeOwned()
    var pageSize = 10
    var failLibraryPage: Int?
    var neverEnding = false
    var libraryOverride: [String: Any]?
    var catalogOverride: [String: Any]?
    var catalogStatus: [String: Int] = [:]
    var maxInflight = 0
    private var inflight = 0

    func calls(host: String? = nil, path: String? = nil) -> [Call] {
        lock.lock(); defer { lock.unlock() }
        return calls.filter { (host == nil || $0.host == host) && (path == nil || $0.path.hasPrefix(path!)) }
    }

    func allow(_ access: String) { lock.lock(); validAccess.insert(access); lock.unlock() }
    private func record(_ call: Call) { lock.lock(); calls.append(call); lock.unlock() }
    private func isGood(_ bearer: String) -> Bool { lock.lock(); defer { lock.unlock() }; return validAccess.contains(bearer) }
    private func countRefresh() { lock.lock(); refreshes += 1; lock.unlock() }
    private func enter() { lock.lock(); inflight += 1; maxInflight = max(maxInflight, inflight); lock.unlock() }
    private func leave() { lock.lock(); inflight -= 1; lock.unlock() }

    private func tokenReply(_ access: String, _ refresh: String) -> Data {
        lock.lock(); validAccess.insert(access); lock.unlock()
        return json(["access_token": access, "expires_in": 28800, "expires_at": "2026-10-23T04:00:00.000Z", "token_type": "bearer",
                     "refresh_token": refresh, "refresh_expires": 115200, "refresh_expires_at": "2026-10-27T04:00:00.000Z",
                     "account_id": ACCOUNT, "client_id": BCDEpicAuth.clientID, "internal_client": true, "client_service": "launcher",
                     "displayName": NAME, "app": "launcher", "in_app_id": ACCOUNT])
    }

    func send(_ request: URLRequest) async throws -> (status: Int, body: Data) {
        let url = request.url!
        let method = request.httpMethod ?? "GET"
        let auth = request.value(forHTTPHeaderField: "Authorization") ?? ""
        let body = request.httpBody.flatMap { String(data: $0, encoding: .utf8) } ?? ""
        record(Call(method: method, host: url.host ?? "", path: url.path, query: url.query ?? "", auth: auth, body: body))
        let bearer = auth.hasPrefix("bearer ") ? String(auth.dropFirst(7)) : ""
        let good = isGood(bearer)
        let unauthorized = json(["errorCode": "errors.com.epicgames.common.authentication.token_verification_failed"])
        switch (url.host ?? "", url.path) {
        case (BCDEpicAuth.accountHost, "/account/api/oauth/token"):
            guard auth == BCDEpicAuth.basicAuthorization else { return (401, json(["errorCode": "errors.com.epicgames.common.oauth.invalid_client"])) }
            let f = formFields(body)
            guard f["token_type"] == "eg1" else { return (400, json(["errorCode": "errors.com.epicgames.common.oauth.invalid_request"])) }
            if f["grant_type"] == "authorization_code" {
                switch f["code"] ?? "" {
                case GOODCODE: return (200, tokenReply(ACCESS1, REFRESH1))
                case "CORRECTIVE0123456789ABCD":
                    return (400, json(["errorCode": "errors.com.epicgames.oauth.corrective_action_required", "correctiveAction": "PRIVACY_POLICY_ACCEPTANCE",
                                       "continuationUrl": "https://www.epicgames.com/id/login/continue?x=1"]))
                default:
                    return (400, json(["errorCode": "errors.com.epicgames.account.oauth.authorization_code_not_found", "errorMessage": "SECRET-MESSAGE " + (f["code"] ?? "")]))
                }
            }
            if f["grant_type"] == "refresh_token" {
                countRefresh()
                try await Task.sleep(nanoseconds: 200_000_000)
                switch f["refresh_token"] ?? "" {
                case REFRESH1: return (200, tokenReply(ACCESS2, REFRESH2))
                case REFRESH2: return (200, tokenReply(ACCESS3, REFRESH3))
                case "revoked0123456789abcdef01234567": return (400, json(["errorCode": "errors.com.epicgames.account.auth_token.invalid_refresh_token"]))
                case "netdown0123456789abcdef01234567": throw URLError(.notConnectedToInternet)
                case "server00123456789abcdef01234567": return (503, Data())
                case "stepreq0123456789abcdef01234567":
                    return (400, json(["errorCode": "errors.com.epicgames.oauth.corrective_action_required", "correctiveAction": "DATE_OF_BIRTH"]))
                default: return (400, json(["errorCode": "errors.com.epicgames.account.auth_token.invalid_refresh_token"]))
                }
            }
            return (400, Data())
        case (BCDEpicAuth.accountHost, "/account/api/oauth/verify"):
            guard good else { return (401, unauthorized) }
            return (200, json(["token": bearer, "session_id": "SESSIONSECRET", "token_type": "bearer", "client_id": BCDEpicAuth.clientID,
                               "internal_client": true, "client_service": "launcher", "account_id": ACCOUNT, "expires_in": 28000,
                               "expires_at": "2026-10-23T04:00:00.000Z", "auth_method": "exchange_code", "display_name": NAME,
                               "app": "launcher", "in_app_id": ACCOUNT, "device_id": "DEVICESECRET"]))
        case (BCDEpicAuth.accountHost, let path) where path.hasPrefix("/account/api/oauth/sessions/kill/"):
            return method == "DELETE" && good ? (204, Data()) : (401, unauthorized)
        case (BCDEpicCatalog.libraryHost, "/library/api/public/items"):
            guard good else { return (401, unauthorized) }
            let items = Dictionary(uniqueKeysWithValues: (URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []).map { ($0.name, $0.value ?? "") })
            guard items["includeMetadata"] == "true" else { return (400, Data()) }
            let start = Int((items["cursor"] ?? "c0").dropFirst()) ?? 0
            if let libraryOverride { return (200, json(libraryOverride)) }
            if neverEnding { return (200, json(["records": [], "responseMetadata": ["nextCursor": "same"]])) }
            if failLibraryPage == start / pageSize { return (500, Data()) }
            let slice = owned[min(start, owned.count)..<min(start + pageSize, owned.count)]
            let records = slice.map { r -> [String: Any] in
                ["namespace": r.ns, "catalogItemId": r.cat, "appName": r.app, "productId": r.cat, "sandboxName": "Live", "sandboxType": r.sandbox,
                 "recordType": "APPLICATION", "acquisitionDate": "2026-01-01T00:00:00.000Z"]
            }
            var reply: [String: Any] = ["records": records]
            reply["responseMetadata"] = start + pageSize < owned.count ? ["nextCursor": "c\(start + pageSize)"] : [String: Any]()
            return (200, json(reply))
        case (BCDEpicCatalog.catalogHost, let path) where path.hasPrefix("/catalog/api/shared/namespace/") && path.hasSuffix("/bulk/items"):
            guard good else { return (401, unauthorized) }
            enter()
            defer { leave() }
            try await Task.sleep(nanoseconds: 40_000_000)
            let namespace = String(path.dropFirst("/catalog/api/shared/namespace/".count).dropLast("/bulk/items".count))
            if let forced = catalogStatus[namespace] { return (forced, Data()) }
            if let catalogOverride { return (200, json(catalogOverride)) }
            let ids = (URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []).filter { $0.name == "id" }.compactMap(\.value)
            var out: [String: Any] = [:]
            for id in ids { if let rec = owned.first(where: { $0.cat == id && $0.ns == namespace }), let e = entry(for: rec) { out[id] = e } }
            return (200, json(out))
        default:
            return (404, Data())
        }
    }
}

// MARK: Phases

func authPhase() async {
    // The login page and the redirect page.
    let login = URLComponents(url: BCDEpicAuth.loginURL, resolvingAgainstBaseURL: false)!
    check(login.scheme == "https" && login.host == "www.epicgames.com" && login.path == "/id/login"
          && login.queryItems?.first(where: { $0.name == "redirectUrl" })?.value == BCDEpicAuth.redirectPage
          && login.percentEncodedQuery == "redirectUrl=https%3A%2F%2Fwww.epicgames.com%2Fid%2Fapi%2Fredirect%3FclientId%3D34a02cf8f4414e29b15921876da36f9a%26responseType%3Dcode",
          "the login page: Epic's own, the redirect page for the launcher client as a fully encoded redirectUrl")
    for good in ["https://www.epicgames.com/id/api/redirect?clientId=34a02cf8f4414e29b15921876da36f9a&responseType=code",
                 "HTTPS://WWW.EPICGAMES.COM/id/api/redirect", "https://www.epicgames.com:443/id/api/redirect?x=1"] {
        check(BCDEpicAuth.isRedirectPage(URL(string: good)!), "the redirect page is recognised: \(good.prefix(60))")
    }
    for bad in ["http://www.epicgames.com/id/api/redirect", "https://www.epicgames.com.evil.example/id/api/redirect",
                "https://evil.example/id/api/redirect?x=www.epicgames.com", "https://www.epicgames.com@evil.example/id/api/redirect",
                "https://user:pw@www.epicgames.com/id/api/redirect", "https://www.epicgames.com:8443/id/api/redirect",
                "https://www.epicgames.com/id/api/redirect/x", "https://www.epicgames.com/id/api/redirectX",
                "https://www.epicgames.com/id/login", "https://store.epicgames.com/id/api/redirect", "https://epicgames.com/id/api/redirect"] {
        check(!BCDEpicAuth.isRedirectPage(URL(string: bad)!), "not the redirect page: \(bad.prefix(64))")
    }

    // What the redirect page's text says.
    let standard = #"{"redirectUrl":"https://localhost/launcher/authorized?code=\#(GOODCODE)","authorizationCode":"\#(GOODCODE)","exchangeCode":null,"sid":null,"ssoV2Enabled":true}"#
    check(BCDEpicAuth.page(from: standard) == .code(GOODCODE), "the redirect page's authorizationCode is the code")
    check(BCDEpicAuth.page(from: "\n  " + standard + "\n") == .code(GOODCODE), "...with whitespace around it")
    check(BCDEpicAuth.page(from: "Response: " + standard + " end") == .code(GOODCODE), "...inside other text")
    check(BCDEpicAuth.page(from: #"{"redirectUrl":"https://localhost/launcher/authorized?code=\#(GOODCODE)","authorizationCode":null}"#) == .code(GOODCODE),
          "a code in a localhost redirectUrl is the code when authorizationCode is null")
    check(BCDEpicAuth.page(from: #"{"redirectUrl":"https://evil.example/x?code=\#(GOODCODE)"}"#) == .other, "a code in another host's redirectUrl is none")
    let corrective = #"{"errorCode":"errors.com.epicgames.oauth.corrective_action_required","correctiveAction":"PRIVACY_POLICY_ACCEPTANCE","continuationUrl":"https://www.epicgames.com/id/login/continue?x=1"}"#
    check(BCDEpicAuth.page(from: corrective) == .corrective(action: "PRIVACY_POLICY_ACCEPTANCE", continuation: URL(string: "https://www.epicgames.com/id/login/continue?x=1")),
          "a corrective action comes with the page for it")
    check(BCDEpicAuth.page(from: corrective.replacingOccurrences(of: "https://www.epicgames.com", with: "https://evil.example")) == .corrective(action: "PRIVACY_POLICY_ACCEPTANCE", continuation: nil),
          "...but a page on another host is not followed")
    check(BCDEpicAuth.page(from: corrective.replacingOccurrences(of: "PRIVACY_POLICY_ACCEPTANCE", with: "<script>x")) == .corrective(action: "UNKNOWN", continuation: URL(string: "https://www.epicgames.com/id/login/continue?x=1")),
          "...and an odd action name is not passed on")
    for none in ["", "not json", "{}", #"{"errorCode":"errors.com.epicgames.common.not_logged_in"}"#, #"{"authorizationCode":"short"}"#,
                 #"{"authorizationCode":"<script>alert(1)</script>0123456789"}"#, #"{"authorizationCode":12345678901234567890}"#, String(repeating: "{", count: 70_000)] {
        check(BCDEpicAuth.page(from: none) == .other, "the page's text gives no code: \(none.prefix(40))")
    }
    for (address, label) in [("https://www.epicgames.com/id/login?redirectUrl=x&token=SECRET", "www.epicgames.com/id/login"),
                             ("https://www.epicgames.com/id/login/epic?lang=en", "www.epicgames.com/id/login/epic"),
                             ("https://www.epicgames.com/id/login/a/b/c/d", "www.epicgames.com/id/login/a"),
                             ("https://www.epicgames.com/id/continue/0123456789abcdef0123456789abcdef/x", "www.epicgames.com/id/continue/*"),
                             ("https://accounts.google.com/o/oauth2/auth?code=SECRET", "other-host"), ("http://www.epicgames.com/id/login", "non-https"),
                             ("https://evil.example/www.epicgames.com", "other-host")] {
        check(BCDEpicAuth.pageLabel(URL(string: address)!) == label, "the log's label for a page: \(label)")
    }
    check(BCDEpicAuth.keys(of: standard) == "authorizationCode,exchangeCode,redirectUrl,sid,ssoV2Enabled" && BCDEpicAuth.keys(of: "garbage") == "not-json"
          && !BCDEpicAuth.keys(of: standard).contains(GOODCODE), "the page's keys for the log: names, never values")

    // A code in an address; a pasted text.
    check(BCDEpicAuth.code(from: URL(string: "http://localhost:6789/launcher/authorized?code=\(GOODCODE)")!) == GOODCODE, "a local redirect carries the code")
    for bad in ["https://evil.example/?code=\(GOODCODE)", "https://localhost.evil.example/?code=\(GOODCODE)", "https://user@localhost/?code=\(GOODCODE)",
                "http://localhost/?code=short", "http://localhost/?code=\(GOODCODE)&code=\(GOODCODE)", "http://localhost/?x=\(GOODCODE)", "ftp://localhost/?code=\(GOODCODE)"] {
        check(BCDEpicAuth.code(from: URL(string: bad)!) == nil, "no code from \(bad.prefix(48))")
    }
    check(BCDEpicAuth.code(fromPasted: GOODCODE) == GOODCODE && BCDEpicAuth.code(fromPasted: "  \(GOODCODE.uppercased())\n") == GOODCODE.uppercased(),
          "pasted: the bare code")
    check(BCDEpicAuth.code(fromPasted: standard) == GOODCODE, "pasted: the page's whole text")
    check(BCDEpicAuth.code(fromPasted: standard.replacingOccurrences(of: "\"", with: "\u{201C}")) == nil
          || BCDEpicAuth.code(fromPasted: standard.replacingOccurrences(of: "\"", with: "\u{201C}")) == GOODCODE, "pasted: curly quotes do not crash")
    let curly = #"{"authorizationCode":"\#(GOODCODE)"}"#.replacingOccurrences(of: "\"", with: "\u{201D}")
    check(BCDEpicAuth.code(fromPasted: curly) == GOODCODE, "pasted: smart quotes from a text editor are straightened")
    check(BCDEpicAuth.code(fromPasted: #""authorizationCode":"\#(GOODCODE)","sid":null"#) == GOODCODE, "pasted: the line without its braces")
    check(BCDEpicAuth.code(fromPasted: "http://localhost/launcher/authorized?code=\(GOODCODE)") == GOODCODE, "pasted: a local redirect address")
    for bad in ["", "hello", "0123456789abcdef0123456789abcdef0", "0123456789abcdef0123456789abcdeg", BCDEpicAuth.redirectPage,
                String(repeating: "a", count: 5000), #"{"authorizationCode":"x"}"#] {
        check(BCDEpicAuth.code(fromPasted: bad) == nil, "pasted: no code in \(bad.prefix(36))")
    }

    // Requests.
    let tokenRequest = BCDEpicAuth.tokenRequest(code: "abc&def=ghi ~")
    check(tokenRequest.httpMethod == "POST" && tokenRequest.url?.host == "account-public-service-prod03.ol.epicgames.com"
          && tokenRequest.url?.path == "/account/api/oauth/token"
          && tokenRequest.value(forHTTPHeaderField: "Content-Type") == "application/x-www-form-urlencoded"
          && tokenRequest.value(forHTTPHeaderField: "Authorization") == "basic " + Data("34a02cf8f4414e29b15921876da36f9a:daafbccc737745039dffe53d94fc76cf".utf8).base64EncodedString()
          && String(data: tokenRequest.httpBody!, encoding: .utf8) == "grant_type=authorization_code&code=abc%26def%3Dghi%20~&token_type=eg1",
          "the token request: POST, the launcher client's Basic header, a strictly encoded form")
    let refreshRequest = BCDEpicAuth.refreshRequest("r+/=")
    check(String(data: refreshRequest.httpBody!, encoding: .utf8) == "grant_type=refresh_token&refresh_token=r%2B%2F%3D&token_type=eg1" && refreshRequest.httpMethod == "POST",
          "the refresh request")
    let verify = BCDEpicAuth.verifyRequest(accessToken: "AAA")
    check(verify.httpMethod == nil || verify.httpMethod == "GET", "the verify request is a GET")
    check(verify.url?.path == "/account/api/oauth/verify" && verify.value(forHTTPHeaderField: "Authorization") == "bearer AAA", "...with the bearer token")
    let kill = BCDEpicAuth.killRequest(accessToken: "eg1~a.b/c")
    check(kill.httpMethod == "DELETE" && kill.url?.absoluteString == "https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/sessions/kill/eg1~a.b%2Fc",
          "the kill request: DELETE, the token percent-encoded in the path")

    // Token replies.
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let base = #"{"access_token":"\#(ACCESS1)","refresh_token":"\#(REFRESH1)","account_id":"\#(ACCOUNT)","displayName":"  \#(NAME) "}"#
    var withSeconds = base
    withSeconds.removeLast()
    withSeconds += #","expires_in":28800,"refresh_expires":115200}"#
    let a = try? BCDEpicAuth.tokens(from: Data(withSeconds.utf8), now: now)
    check(a?.accessExpires == now.addingTimeInterval(28800) && a?.refreshExpires == now.addingTimeInterval(115200) && a?.accountID == ACCOUNT && a?.displayName == NAME,
          "the token reply: lifetimes from expires_in and refresh_expires, the account, the trimmed name")
    let iso = ISO8601DateFormatter()
    iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    var withDates = base
    withDates.removeLast()
    withDates += #","expires_at":"\#(iso.string(from: now.addingTimeInterval(7200)))","refresh_expires_at":"\#(iso.string(from: now.addingTimeInterval(86400)))"}"#
    let b = try? BCDEpicAuth.tokens(from: Data(withDates.utf8), now: now)
    check(b != nil && abs(b!.accessExpires.timeIntervalSince(now) - 7200) < 0.01 && abs(b!.refreshExpires!.timeIntervalSince(now) - 86400) < 0.01,
          "...or from expires_at and refresh_expires_at when there are no seconds")
    let snake = try? BCDEpicAuth.tokens(from: Data(#"{"access_token":"\#(ACCESS1)","refresh_token":"\#(REFRESH1)","accountId":"\#(ACCOUNT)","display_name":"\#(NAME)","expires_in":"3600"}"#.utf8), now: now)
    check(snake?.displayName == NAME && snake?.accountID == ACCOUNT && snake?.accessExpires == now.addingTimeInterval(3600),
          "...with either spelling of the keys and seconds as a string")
    let bare = try? BCDEpicAuth.tokens(from: Data(#"{"access_token":"\#(ACCESS1)","refresh_token":"\#(REFRESH1)"}"#.utf8), now: now)
    check(bare?.accessExpires == now.addingTimeInterval(3600) && bare?.refreshExpires == nil && bare?.accountID == nil, "...an hour when it says nothing, no account until verify")
    let huge = try? BCDEpicAuth.tokens(from: Data(#"{"access_token":"\#(ACCESS1)","refresh_token":"\#(REFRESH1)","expires_in":99999999999,"refresh_expires":1}"#.utf8), now: now)
    check(huge?.accessExpires == now.addingTimeInterval(7 * 86400) && huge?.refreshExpires == now.addingTimeInterval(60), "...lifetimes are bounded")
    let kept = try? BCDEpicAuth.tokens(from: Data(#"{"access_token":"\#(ACCESS2)","expires_in":3600}"#.utf8), now: now, previous: a)
    check(kept?.refreshToken == REFRESH1 && kept?.accountID == ACCOUNT && kept?.displayName == NAME && kept?.refreshExpires == nil,
          "a refresh keeps the refresh token, account and name it did not send")
    for bad in [#"{"refresh_token":"\#(REFRESH1)"}"#, #"{"access_token":"has space here","refresh_token":"\#(REFRESH1)"}"#, #"{"access_token":"\#(ACCESS1)"}"#,
                #"{"access_token":"short","refresh_token":"\#(REFRESH1)"}"#, "[]", "x"] {
        check((try? BCDEpicAuth.tokens(from: Data(bad.utf8), now: now)) == nil, "a token reply that is not one is refused: \(bad.prefix(40))")
    }
    check("\(a!)" == "BCDEpicTokens(<redacted>)" && String(reflecting: a!) == "BCDEpicTokens(<redacted>)" && "\(String(describing: a))" == "Optional(BCDEpicTokens(<redacted>))",
          "printing the tokens shows nothing of them")
    check(a!.needsRefresh(now: now.addingTimeInterval(28800 - 599)) && !a!.needsRefresh(now: now.addingTimeInterval(28800 - 601)),
          "a token is refreshed ten minutes before it lapses")

    // Dates from numbers against Foundation's.
    var dateMatches = true
    var seed: UInt64 = 7
    func next() -> UInt64 { seed = seed &* 6364136223846793005 &+ 1442695040888963407; return seed >> 33 }
    for _ in 0..<400 {
        let seconds = Double(next() % 4_000_000_000) + 1
        let text = iso.string(from: Date(timeIntervalSince1970: seconds + 0.25))
        if let value = BCDEpicAuth.epoch(iso: text) { dateMatches = dateMatches && abs(value - (seconds + 0.25)) < 0.002 } else { dateMatches = false }
    }
    check(dateMatches, "ISO 8601 dates are read from numbers as Foundation reads them (400 dates, fractions)")
    check(BCDEpicAuth.epoch(iso: "2026-10-23T04:00:00Z") == 1_792_728_000
          && BCDEpicAuth.epoch(iso: "1970-01-01T00:00:00.000Z") == 0 && BCDEpicAuth.epoch(iso: "2000-02-29T12:00:00.5Z") == 951_825_600.5,
          "...a leap day, the epoch, no fraction")
    for bad in ["", "2026-10-23", "2026-10-23T04:00:00", "2026-13-23T04:00:00Z", "2026-10-32T04:00:00Z", "2026-10-23T25:00:00Z", "26-10-23T04:00:00Z",
                "2026-10-23T04:00:00.Z", "2026-10-23T04:00:00.1234567890Z", "2026-10-23 04:00:00Z", "1969-12-31T23:59:59Z", "2026-10-23T04:00:00+02:00"] {
        check(BCDEpicAuth.epoch(iso: bad) == nil, "not a date: \(bad)")
    }

    // Error replies.
    check(BCDEpicAuth.failure(status: 400, body: json(["errorCode": "errors.com.epicgames.account.invalid_account_credentials"]))
          == .rejected(status: 400, code: "errors.com.epicgames.account.invalid_account_credentials"), "an error reply: its status and Epic's error code")
    check(BCDEpicAuth.failure(status: 503, body: Data()) == .http(503) && BCDEpicAuth.failure(status: 429, body: Data()) == .http(429), "a server or rate-limit answer is a try-again-later")
    check(BCDEpicAuth.failure(status: 400, body: json(["errorCode": "x y<z>"])) == .rejected(status: 400, code: ""), "an odd error code is not kept")
    check(BCDEpicAuth.failure(status: 400, body: Data("not json".utf8)) == .rejected(status: 400, code: ""), "...nor is a body that is no JSON")
    check(BCDEpicError.rejected(status: 401, code: "").refusesRefresh && BCDEpicError.rejected(status: 400, code: "").refusesRefresh
          && !BCDEpicError.rejected(status: 404, code: "").refusesRefresh && !BCDEpicError.http(503).refusesRefresh, "which answers to a refresh end the sign-in")
    for error in [BCDEpicError.signedOut, .signInRefused, .corrective(action: "DATE_OF_BIRTH", continuation: nil), .rejected(status: 400, code: "a.b"), .http(500), .badReply("token")] {
        check(!(error.errorDescription ?? "").isEmpty && !(error.errorDescription ?? "").contains("a.b") && error.logReason.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" || $0 == "." ) },
              "a message for the player and a clean reason for the log: \(error.logReason)")
    }

    // The session.
    let secrets = BCDMemorySecrets()
    let clock = Clock()
    let stub = StubEpic()
    let session = BCDEpicSession(secrets: secrets, transport: stub, now: { clock.now })
    await expectError("a wrong code is refused", .rejected(status: 400, code: "errors.com.epicgames.account.oauth.authorization_code_not_found")) {
        _ = try await session.signIn(code: "BADCODE0123456789ABCDEF")
    }
    check(secrets.read(BCDEpicSession.itemName) == nil, "nothing is kept after a refused sign-in")
    await expectError("a corrective action is reported", .corrective(action: "PRIVACY_POLICY_ACCEPTANCE", continuation: URL(string: "https://www.epicgames.com/id/login/continue?x=1"))) {
        _ = try await session.signIn(code: "CORRECTIVE0123456789ABCD")
    }
    do {
        let tokens = try await session.signIn(code: GOODCODE)
        check(tokens.accountID == ACCOUNT && tokens.displayName == NAME && tokens.accessToken == ACCESS1, "signed in: the tokens, the account and its name")
        check(secrets.read(BCDEpicSession.itemName) != nil, "...kept in the store's one secret item")
        let sent = stub.calls(host: BCDEpicAuth.accountHost)
        check(sent.map { $0.path } == ["/account/api/oauth/token", "/account/api/oauth/token", "/account/api/oauth/token", "/account/api/oauth/verify"]
              && sent.last?.auth == "bearer " + ACCESS1, "...the code's exchange, then verify with the new token")
        check(try await session.accessToken() == ACCESS1 && stub.refreshes == 0, "a fresh token is used as it is")
        clock.advance(28800 - 500)
        let seen = try await withThrowingTaskGroup(of: String.self) { group -> [String] in
            for _ in 0..<5 { group.addTask { try await session.accessToken() } }
            var all: [String] = []
            for try await t in group { all.append(t) }
            return all
        }
        check(seen.allSatisfy { $0 == ACCESS2 } && stub.refreshes == 1, "five callers near the lapse share one refresh (\(stub.refreshes))")
        let reread = BCDEpicSession(secrets: secrets, transport: stub, now: { clock.now })
        let stored = await reread.current()
        check(stored?.accessToken == ACCESS2 && stored?.refreshToken == REFRESH2 && stored?.accountID == ACCOUNT, "the refreshed tokens are what the item holds")

        // An API call refused with 401 is tried again once after a refresh.
        let api = BCDEpicAPI(session: reread, transport: stub)
        var stale = stored!
        stale.accessToken = "eg1~STALE.TOKEN.HERE"
        stale.accessExpires = clock.now.addingTimeInterval(3600)
        try secrets.write(BCDEpicSession.itemName, JSONEncoder().encode(stale))
        let apiSession = BCDEpicSession(secrets: secrets, transport: stub, now: { clock.now })
        let retried = BCDEpicAPI(session: apiSession, transport: stub)
        let before = stub.refreshes
        let page = try await retried.get(BCDEpicCatalog.libraryURL(cursor: nil))
        check(page.status == 200 && stub.refreshes == before + 1
              && stub.calls(host: BCDEpicCatalog.libraryHost).map { $0.auth } == ["bearer eg1~STALE.TOKEN.HERE", "bearer " + ACCESS3],
              "an API call refused with 401 is tried again once with a refreshed token")
        await expectError("the token goes only to Epic's own hosts", .badReply("host")) { _ = try await api.get(URL(string: "https://evil.example/x")!) }
        await expectError("...and only over https", .badReply("host")) { _ = try await api.get(URL(string: "http://library-service.live.use1a.on.epicgames.com/x")!) }
        check(BCDEpicAPI.trusted(URL(string: "https://catalog-public-service-prod06.ol.epicgames.com/x")!)
              && !BCDEpicAPI.trusted(URL(string: "https://epicgames.com.evil.example/x")!) && !BCDEpicAPI.trusted(URL(string: "https://evil.example/.epicgames.com")!)
              && !BCDEpicAPI.trusted(URL(string: "https://x.epicgames.com:8443/x")!) && !BCDEpicAPI.trusted(URL(string: "https://u@x.epicgames.com/x")!),
              "which addresses may carry the token")

        // Verify, with a refresh when the session has lapsed.
        let verified = try await apiSession.verify()
        check(verified.displayName == NAME && verified.accountID == ACCOUNT, "verify: the account's name again")
    } catch { check(false, "session: \(error)") }

    // A refresh Epic refuses signs out; one that fails for want of a network or a server does not.
    func makeSession(withRefresh refresh: String, access: String = "eg1~OLD.ACCESS.TOKEN") async -> (BCDEpicSession, BCDMemorySecrets) {
        let secrets = BCDMemorySecrets()
        let held = BCDEpicTokens(accessToken: access, refreshToken: refresh, accessExpires: clock.now, refreshExpires: nil, accountID: ACCOUNT, displayName: NAME)
        try? secrets.write(BCDEpicSession.itemName, JSONEncoder().encode(held))
        return (BCDEpicSession(secrets: secrets, transport: stub, now: { clock.now }), secrets)
    }
    do {
        let (revoked, kept) = await makeSession(withRefresh: "revoked0123456789abcdef01234567")
        await expectError("a refused refresh fails as a sign-in Epic no longer accepts", .signInRefused) { _ = try await revoked.accessToken() }
        let after = await revoked.current()
        check(after == nil && kept.read(BCDEpicSession.itemName) == nil, "...and signs out: the item is gone")
        for (refresh, error) in [("netdown0123456789abcdef01234567", nil), ("server00123456789abcdef01234567", BCDEpicError.http(503)),
                                 ("stepreq0123456789abcdef01234567", BCDEpicError.corrective(action: "DATE_OF_BIRTH", continuation: nil))] as [(String, BCDEpicError?)] {
            let (s, store) = await makeSession(withRefresh: refresh)
            await expectError("a refresh that fails with \(refresh.prefix(7)) is an error", error) { _ = try await s.accessToken() }
            check(store.read(BCDEpicSession.itemName) != nil, "...and the sign-in stays (\(refresh.prefix(7)))")
        }
    }

    // Verify when the stored session has lapsed: refresh, then verify.
    do {
        let (lapsed, store) = await makeSession(withRefresh: REFRESH2, access: "eg1~LAPSED.ACCESS.TOKEN")
        let verified = try await lapsed.verify()
        check(verified.accessToken == ACCESS3 && store.read(BCDEpicSession.itemName) != nil, "verify refreshes a lapsed session first")
        let (dead, deadStore) = await makeSession(withRefresh: "revoked0123456789abcdef01234567")
        await expectError("verify of a revoked session signs out", .signInRefused) { _ = try await dead.verify() }
        check(deadStore.read(BCDEpicSession.itemName) == nil, "...the item is gone")
        let empty = BCDEpicSession(secrets: BCDMemorySecrets(), transport: stub, now: { clock.now })
        await expectError("with nobody signed in", .signedOut) { _ = try await empty.accessToken() }
    } catch { check(false, "verify: \(error)") }

    // Sign-out.
    do {
        let out = BCDMemorySecrets()
        let signedIn = BCDEpicSession(secrets: out, transport: stub, now: { clock.now })
        _ = try await signedIn.signIn(code: GOODCODE)
        let revoked = await signedIn.signOut()
        let after = await signedIn.current()
        check(revoked && out.read(BCDEpicSession.itemName) == nil && after == nil, "sign-out: Epic ends the session and the item is gone")
        check(stub.calls().contains { $0.method == "DELETE" && $0.path.hasPrefix("/account/api/oauth/sessions/kill/") }, "...with a DELETE of the session")
        let nobody = BCDEpicSession(secrets: BCDMemorySecrets(), transport: stub, now: { clock.now })
        let none = await nobody.signOut()
        check(!none, "sign-out with nobody signed in does nothing")
        let (offline, offlineStore) = await makeSession(withRefresh: REFRESH1, access: "eg1~NOT.VALID.TOKEN")
        let notRevoked = await offline.signOut()
        check(!notRevoked && offlineStore.read(BCDEpicSession.itemName) == nil, "sign-out when Epic refuses the kill still forgets the tokens")
    } catch { check(false, "sign-out: \(error)") }
}

// A URLProtocol answering Epic's hosts, for the transport.
final class StubWire: URLProtocol {
    nonisolated(unsafe) static var seen: [(url: String, agent: String)] = []
    static let lock = NSLock()
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host?.hasSuffix(".epicgames.com") ?? false }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let url = request.url!
        StubWire.lock.lock(); StubWire.seen.append((url.absoluteString, request.value(forHTTPHeaderField: "User-Agent") ?? "")); StubWire.lock.unlock()
        let (status, headers, body): (Int, [String: String], Data) = {
            switch url.path {
            case "/redirect": return (302, ["Location": "https://evil.epicgames.com/final"], Data())
            case "/error": return (400, [:], Data(#"{"errorCode":"errors.com.epicgames.test"}"#.utf8))
            default: return (200, [:], Data("ok".utf8))
            }
        }()
        let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

func transportPhase() async {
    let transport = BCDEpicURLSession(userAgent: "TestAgent/1", protocols: [StubWire.self])
    do {
        let ok = try await transport.send(URLRequest(url: URL(string: "https://x.epicgames.com/fine")!))
        check(ok.status == 200 && String(data: ok.body, encoding: .utf8) == "ok", "the transport returns a status and a body")
        let failed = try await transport.send(URLRequest(url: URL(string: "https://x.epicgames.com/error")!))
        check(failed.status == 400 && String(data: failed.body, encoding: .utf8)?.contains("errors.com.epicgames.test") == true, "...also for an error status (Epic's code is in the body)")
        let redirected = try await transport.send(URLRequest(url: URL(string: "https://x.epicgames.com/redirect")!))
        check(redirected.status == 302 && !StubWire.seen.contains { $0.url.contains("evil") }, "a redirect is not followed: a token never travels on")
        check(StubWire.seen.allSatisfy { $0.agent == "TestAgent/1" }, "every request carries the user agent")
        check(BCDEpicAuth.launcherUserAgent.hasPrefix("UELauncher/") && !BCDEpicAuth.launcherUserAgent.contains("Mozilla"), "the default user agent is launcher-shaped")
    } catch { check(false, "transport: \(error)") }
}

func libraryPhase() async {
    // One page of the library service.
    let page = try? BCDEpicCatalog.parseLibraryPage(["records": [
        ["namespace": "nsa", "catalogItemId": catID(1), "appName": "Alpha", "sandboxType": "PUBLIC"],
        ["namespace": "ue", "catalogItemId": catID(2), "appName": "Asset"],
        ["namespace": "nsp", "catalogItemId": catID(3), "appName": "Private", "sandboxType": "private"],
        ["namespace": "nsq", "catalogItemId": catID(4), "appName": "has space"],
        ["namespace": "nsq", "catalogItemId": catID(5)],
        ["namespace": "", "catalogItemId": catID(6), "appName": "NoNs"],
        ["namespace": "nsr", "catalogItemId": "../x", "appName": "Bad"],
    ], "responseMetadata": ["nextCursor": "abc123=="]])
    check(page?.records == [BCDEpicRecord(appName: "Alpha", namespace: "nsa", catalogItemId: catID(1))] && page?.next == "abc123=="
          && page?.raw == 7 && page?.droppedUnreal == 1 && page?.droppedPrivate == 1 && page?.droppedInvalid == 4,
          "a library page: Unreal Engine's namespace, private sandboxes and unusable records dropped, the cursor kept")
    check((try? BCDEpicCatalog.parseLibraryPage(["records": [], "responseMetadata": ["nextCursor": "has space"]]))?.next == nil
          && (try? BCDEpicCatalog.parseLibraryPage(["records": [], "responseMetadata": ["nextCursor": ""]]))?.next == nil
          && (try? BCDEpicCatalog.parseLibraryPage(["records": []]))?.next == nil, "a cursor with a space, an empty one or none ends the pages")
    let other = try? BCDEpicCatalog.parseLibraryPage(["items": [["namespace": "nsa", "catalogItemId": catID(1), "appName": "Alpha"]]])
    check(other?.records.count == 1, "the records are found under another key when they are the list of records")
    check((try? BCDEpicCatalog.parseLibraryPage(["error": "x"])) == nil && (try? BCDEpicCatalog.parseLibraryPage(["records": "x"])) == nil, "a reply with no records is refused")
    check(BCDEpicCatalog.libraryURL(cursor: nil).absoluteString == "https://library-service.live.use1a.on.epicgames.com/library/api/public/items?includeMetadata=true"
          && BCDEpicCatalog.libraryURL(cursor: "a b+/=").absoluteString.hasSuffix("&cursor=a%20b%2B%2F%3D"), "the library address, the cursor percent-encoded")
    check(BCDEpicCatalog.bulkURL(namespace: "ns a", ids: ["i1", "i2"]).absoluteString
          == "https://catalog-public-service-prod06.ol.epicgames.com/catalog/api/shared/namespace/ns%20a/bulk/items?id=i1&id=i2&includeDLCDetails=true&includeMainGameDetails=true&country=US&locale=en-US",
          "the catalog address: the namespace in the path, each ID, DLC and main-game details, US and en-US")

    // Catalog entries.
    func details(_ app: String, ns: String = "nsa", cat: Int = 1) -> BCDEpicCatalog.Details? {
        guard let e = entry(for: Rec(app: app, ns: ns, cat: catID(cat))) else { return nil }
        return BCDEpicCatalog.parseBulk([catID(cat): e])[catID(cat)]
    }
    let alpha = details("Alpha")
    check(alpha?.kind == .game && alpha?.title == "Title of Alpha" && alpha?.tall == "https://cdn1.epicgames.com/Alpha_tall.jpg" && alpha?.wide == "https://cdn1.epicgames.com/Alpha_wide.jpg"
          && alpha?.folderName == "AlphaFolder" && alpha?.canRunOffline == true && alpha?.ownershipToken == false && alpha?.thirdParty == nil && alpha?.accessControl == nil,
          "a game: title, the box art by type, the attributes Epic's catalog names")
    let beta = details("Beta")
    check(beta?.tall == "https://cdn2.unrealengine.com/beta_tall.jpg" && beta?.wide == "https://cdn2.unrealengine.com/beta_wide.jpg" && beta?.accessControl == true && beta?.ownershipToken == true,
          "the offer images when there is no box art, http made https, attribute flags in any case")
    let generic = details("Generic")
    check(generic?.tall == "https://cdn1.epicgames.com/g_tall.jpg" && generic?.wide == "https://cdn1.epicgames.com/g_wide.jpg" && generic?.kind == .game,
          "pictures of unknown types by their shape, a game with no platforms named stays listed")
    check(details("AlphaDLC", cat: 3)?.kind == .dlc && details("ModPack", ns: "nsm", cat: 4)?.kind == .mod && details("MacOnly", ns: "nsx", cat: 7)?.kind == .notWindows
          && details("Extra", ns: "nse", cat: 9)?.kind == .extra && details("Third", ns: "nst", cat: 10)?.thirdParty == "Ubisoft",
          "DLC (it names a main game), mods, extras, no Windows build; a third-party launcher noted")
    check(BCDEpicCatalog.kind(["mainGameItem": NSNull(), "categories": [["path": "games"]]]) == .game && BCDEpicCatalog.kind(["categories": [["path": "addons"], ["path": "games"]]]) == .game,
          "a null main game is no DLC; a game that is also an add-on category stays a game")
    check(BCDEpicCatalog.parseBulk(["x": ["id": catID(1), "title": "  "], "y": "nope", "z": [["id": catID(2), "title": "In a list"]]])[catID(2)]?.title == "In a list"
          && BCDEpicCatalog.parseBulk(["x": ["id": catID(1), "title": "  "]]).isEmpty, "an entry with no title is no game; entries in a list are read too")
    check(BCDEpicCatalog.imageURL("http://a.example/x.jpg") == "https://a.example/x.jpg" && BCDEpicCatalog.imageURL("ftp://a.example/x") == nil
          && BCDEpicCatalog.imageURL("https://u:p@a.example/x") == nil && BCDEpicCatalog.imageURL(String(repeating: "a", count: 700)) == nil && BCDEpicCatalog.imageURL(nil) == nil,
          "picture addresses: https only, no credentials, bounded")
    let item = BCDEpicItem(id: "Alpha", namespace: "nsa", catalogItemId: catID(1), kind: .game, title: "A", tall: "https://cdn1.epicgames.com/t.jpg", wide: "https://cdn1.epicgames.com/w.jpg")
    let art = BCDEpicCatalog.art(item)
    check(art.cover.map(\.lastPathComponent) == ["t.jpg", "w.jpg"] && art.header.map(\.lastPathComponent) == ["w.jpg", "t.jpg"] && art.hero.map(\.lastPathComponent) == ["w.jpg"],
          "a game's art lists: the portrait first for the cover, the wide picture for the header and hero")
    check(BCDEpicCatalog.art(BCDEpicItem(id: "X", namespace: "n", catalogItemId: "c")) == BCDEpicArt(), "no pictures, no art")
    let key = BCDEpicCatalog.accountKey(ACCOUNT)
    check(key.count == 64 && !key.contains(ACCOUNT) && key == BCDEpicCatalog.accountKey(ACCOUNT) && key != BCDEpicCatalog.accountKey(ACCOUNT + "x"), "the cache's account key is a hash")

    // The refresh, end to end, against the stand-in.
    let clock = Clock()
    let stub = StubEpic()
    stub.allow(ACCESS1)
    let secrets = BCDMemorySecrets()
    do {
        let tokens = BCDEpicTokens(accessToken: ACCESS1, refreshToken: REFRESH1, accessExpires: clock.now.addingTimeInterval(20000), refreshExpires: nil, accountID: ACCOUNT, displayName: NAME)
        try secrets.write(BCDEpicSession.itemName, JSONEncoder().encode(tokens))
        let session = BCDEpicSession(secrets: secrets, transport: stub, now: { clock.now })
        let api = BCDEpicAPI(session: session, transport: stub)
        let started = Date()
        let first = try await BCDEpicLibrary.refresh(api: api, known: [], now: clock.now, batchSize: 25)
        let stats = first.stats
        let owned = stub.owned
        check(stats.pages == 5 && stats.records == owned.count && stats.statuses[200, default: 0] >= 5, "the pages are read by their cursors (\(stats.pages) pages, \(stats.records) records)")
        check(stats.droppedUnreal == 1 && stats.droppedPrivate == 1 && stats.droppedInvalid == 1 && stats.duplicates == 1, "the filters count what they dropped")
        let byID = Dictionary(first.items.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        check(first.items.count == owned.count - 4, "every other record is an item once (\(first.items.count))")
        check(byID["Alpha"]?.kind == .game && byID["Alpha"]?.title == "Title of Alpha" && byID["Alpha"]?.fetched == clock.now && byID["Beta"]?.kind == .game
              && byID["AlphaDLC"]?.kind == .dlc && byID["ModPack"]?.kind == .mod && byID["MacOnly"]?.kind == .notWindows && byID["Extra"]?.kind == .extra
              && byID["Third"]?.thirdParty == "Ubisoft" && byID["Generic"]?.kind == .game, "each item is classified from its catalog entry")
        check(byID["NoInfo"]?.kind == .unknown && byID["Flaky"]?.kind == .game, "an item the catalog does not know stays unknown")
        check(stats.games == 5 + 30 + 5 && stats.dlc == 1 && stats.mods == 1 && stats.notWindows == 1 && stats.extras == 1 && stats.unknown == 1 && stats.thirdParty == 1,
              "the counts of the kinds: games=\(stats.games) dlc=\(stats.dlc) unknown=\(stats.unknown)")
        check(stub.maxInflight <= 4 && stub.maxInflight >= 2, "at most four catalog calls at a time (saw \(stub.maxInflight))")
        let bulk = stub.calls(host: BCDEpicCatalog.catalogHost)
        let pack = bulk.filter { $0.path.contains("/nspack/") }
        check(pack.count == 1 && pack[0].query.split(separator: "&").filter { $0.hasPrefix("id=") }.count == 5, "the items of one namespace go in one catalog call")
        check(stats.namespaces == bulk.count && stats.catalogRequests == bulk.count && stats.catalogFailed == 0, "one catalog call per namespace (\(bulk.count))")
        check(Date().timeIntervalSince(started) < 30, "the refresh is quick against a quick server")
        BCDEpicLog.write("[epic-library] refreshed " + stats.summary)
        let smallBatches = stub.calls(host: BCDEpicCatalog.catalogHost).count
        _ = smallBatches

        // A second refresh reuses what is fresh; unknown items are asked again.
        let before = stub.calls(host: BCDEpicCatalog.catalogHost).count
        let second = try await BCDEpicLibrary.refresh(api: api, known: first.items, now: clock.now.addingTimeInterval(3600))
        let asked = stub.calls(host: BCDEpicCatalog.catalogHost).count - before
        check(asked == 1 && second.stats.reused == second.items.count - 1 && second.items.map(\.id) == first.items.map(\.id), "a second refresh asks only for the unknown item (\(asked) call)")
        // Stale entries are read again; a call that fails keeps the old entry.
        var old = first.items
        for i in old.indices where old[i].id == "Alpha" || old[i].id == "Beta" {
            old[i].fetched = clock.now.addingTimeInterval(-15 * 86400)
            old[i].title = "Old " + old[i].id
        }
        stub.catalogStatus["nsb"] = 500
        let third = try await BCDEpicLibrary.refresh(api: api, known: old, now: clock.now)
        let t = Dictionary(third.items.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        check(t["Alpha"]?.title == "Title of Alpha" && t["Beta"]?.title == "Old Beta" && third.stats.catalogFailed >= 1 && third.stats.statuses[500, default: 0] >= 1,
              "a stale entry is read again; when Epic's server fails the old entry stays")
        stub.catalogStatus.removeAll()

        // Small batches split a namespace; a page that fails fails the refresh whole.
        let beforeSplit = stub.calls(host: BCDEpicCatalog.catalogHost).filter { $0.path.contains("/nspack/") }.count
        _ = try await BCDEpicLibrary.refresh(api: api, known: [], now: clock.now, batchSize: 2)
        let split = stub.calls(host: BCDEpicCatalog.catalogHost).filter { $0.path.contains("/nspack/") }.count - beforeSplit
        check(split == 3, "a namespace's items are asked for in batches (five items, two at a time: \(split) calls)")
        stub.failLibraryPage = 2
        await expectError("a library page that fails fails the refresh", .http(500)) { _ = try await BCDEpicLibrary.refresh(api: api, known: first.items, now: clock.now) }
        stub.failLibraryPage = nil
        stub.neverEnding = true
        let looping = try await BCDEpicLibrary.refresh(api: api, known: [], now: clock.now)
        check(looping.stats.pages == 2 && looping.items.isEmpty, "a cursor that comes back ends the pages (\(looping.stats.pages) pages)")
        let capped = try await BCDEpicLibrary.refresh(api: api, known: [], now: clock.now, maxPages: 1)
        check(capped.stats.pages == 1, "the page cap holds")
        stub.neverEnding = false

        // A reply BCD cannot read: the log says what it looked like (keys and kinds), never a value.
        stub.libraryOverride = ["data": [["oddKey": "SECRETVALUE", "n": 3, "t": true, "z": NSNull()]], "count": 1, "meta": ["next": "SECRETVALUE2"]]
        await expectError("a library reply with no records is refused", .badReply("library")) { _ = try await BCDEpicLibrary.refresh(api: api, known: [], now: clock.now) }
        stub.libraryOverride = nil
        stub.catalogOverride = ["items": [["id": catID(1), "name": "SECRETVALUE3", "x": 1]], "count": 1]
        let odd = try await BCDEpicLibrary.refresh(api: api, known: [], now: clock.now)
        check(odd.stats.unknown == odd.items.count && odd.stats.games == 0, "a catalog reply with no entries in a known shape leaves every item unknown")
        stub.catalogOverride = nil
        check(BCDEpicLog.shape(["records": [["a": 1, "b": "VALUE", "c": true, "d": NSNull(), "e": [1], "f": ["g": 1]]], "responseMetadata": ["nextCursor": "VALUE2"],
                                catID(5): ["title": "VALUE3"], catID(6): ["title": "VALUE4"], "bad key!": 1])
              == "top[records=a,responseMetadata=o,ids=2] records=1 first[a=n,b=s,c=b,d=N,e=a,f=o] entry[title=s]",
              "a reply's shape for the log: keys and kinds, IDs counted, no value")

        // The cache holds no secret.
        let cache = BCDEpicCatalog.Cache(account: key, updated: clock.now, items: first.items)
        let text = String(data: try JSONEncoder().encode(cache), encoding: .utf8)!
        check(!SECRETS.contains { text.contains($0) } && text.contains(key) && !text.lowercased().contains("token\":\"eg1"), "the cached list holds no token, code, name or account ID")
        let back = try JSONDecoder().decode(BCDEpicCatalog.Cache.self, from: Data(text.utf8))
        check(back.items == first.items && back.account == key && back.version == 1, "...and reads back as it was")

        // A refused sign-in during the catalog calls fails the refresh as a sign-out would.
        let revokedSecrets = BCDMemorySecrets()
        let lapsed = BCDEpicTokens(accessToken: "eg1~GONE.GONE.GONE", refreshToken: "revoked0123456789abcdef01234567", accessExpires: clock.now.addingTimeInterval(20000), refreshExpires: nil, accountID: ACCOUNT, displayName: nil)
        try revokedSecrets.write(BCDEpicSession.itemName, JSONEncoder().encode(lapsed))
        let gone = BCDEpicAPI(session: BCDEpicSession(secrets: revokedSecrets, transport: stub, now: { clock.now }), transport: stub)
        await expectError("a refused sign-in is thrown", .signInRefused) { _ = try await BCDEpicLibrary.refresh(api: gone, known: [], now: clock.now) }
        check(revokedSecrets.read(BCDEpicSession.itemName) == nil, "...and the tokens are gone")
    } catch { check(false, "library: \(error)") }
}

func logPhase() {
    // Masks on made-up lines.
    let lines = [
        "token eg1~ABC.DEF.GHI and egoc1~XYZ.abc.def end",
        "code 0123456789abcdef0123456789abcdef and ID A1B2C3D4E5F60718293A4B5C6D7E8F90 and 0123456789abcdef0123456789abcde",
        #"{"authorizationCode":"0123456789abcdef0123456789abcdef","displayName":"Some Name","account_id":"abc","sid":"s1"}"#,
        "authorizationCode=ABCDEFGH12345678&exchange_code=ZZZZ1234 user_name: Bob display_name = \"Jo Hn\"",
        "mail someone@example.com bearer abc.def.ghi access_token=AAA refresh_token: BBB password=pw",
        "plain line with 12 files, 3 games and status=200 ms=45 (inside: ok)",
    ]
    // What BCDEpicLog.write does to a line before it reaches the app's log.
    let out = lines.map { BCDContentLog.redact(BCDEpicLog.redact($0)) }
    check(!out[0].contains("ABC.DEF") && !out[0].contains("XYZ.abc") && out[0].contains("eg1~<redacted>") && out[0].contains("egoc1~<redacted>"), "log: Epic's two token families are masked")
    check(!out[1].contains("0123456789abcdef0123456789abcdef") && !out[1].lowercased().contains("a1b2c3d4e5f6") && out[1].contains("0123456789abcdef0123456789abcde"),
          "log: a 32-digit hexadecimal run (account ID, code, refresh token) is masked, other lengths are not")
    check(!out[2].contains("Some Name") && !out[2].contains("0123456789abcdef") && !out[2].contains("abc\"") && !out[2].contains("s1"), "log: JSON keys that hold names, codes and IDs are masked")
    check(!out[3].contains("ABCDEFGH12345678") && !out[3].contains("ZZZZ1234") && !out[3].contains("Bob") && !out[3].contains("Jo Hn"), "log: key=value forms are masked")
    check(!out[4].contains("someone@") && !out[4].contains("abc.def.ghi") && !out[4].contains("AAA") && !out[4].contains("BBB") && !out[4].contains("=pw"), "log: an e-mail address, a bearer token, tokens and a password are masked")
    check(out[5] == lines[5], "log: an ordinary line with counts and statuses is untouched")
    check(BCDEpicLog.mask(ACCOUNT) == "a1..90" && BCDEpicLog.mask("short") == ".." && BCDEpicLog.mask("") == "..", "log: an account ID shows its first and last two characters")
    check(BCDEpicLog.statuses([200: 12, 404: 1, 500: 2]) == "200x12,404x1,500x2" && BCDEpicLog.statuses([:]) == "none", "log: statuses with counts")
    check(BCDEpicLog.reason(BCDEpicError.http(503)) == "http-503" && BCDEpicLog.reason(URLError(.timedOut)) == "net--1001" && BCDEpicLog.reason(BCDContentError.http(401)) == "http-401"
          && BCDEpicLog.reason(NSError(domain: "x", code: 1)) == "other", "log: a short reason for any error")

    logLock.lock(); let all = logs; logLock.unlock()
    let joined = all.joined(separator: "\n")
    let leaked = SECRETS.filter { joined.contains($0) }
    check(leaked.isEmpty, "no token, code, name or account ID in any log line of the whole run \(leaked.isEmpty ? "" : "(leaked: \(leaked.count))")")
    check(!joined.contains("eg1~") && !joined.contains("egoc1~") && !joined.lowercased().contains("secret-message") && !joined.contains("@"), "...nor a token prefix or an e-mail address")
    check(all.allSatisfy { $0.hasPrefix("[epic-auth]") || $0.hasPrefix("[epic-library]") }, "every line the run logged is an [epic-auth] or [epic-library] line")
    check(all.contains { $0 == "[epic-library] reply is not a library page: top[count=n,data=a,meta=o] data=1 first[n=n,oddKey=s,t=b,z=N] entry[next=s]" }
          && !joined.contains("SECRETVALUE"), "an unreadable reply is logged by its shape, never a value")
    check(all.contains { $0.hasPrefix("[epic-library] catalog reply gave no entry: top[count=n,items=a] items=1 first[id=s,name=s,x=n]") }, "...and so is a catalog reply with no entries")
    check(all.contains { $0.hasPrefix("[epic-library] refreshed records=49 pages=5 games=40 dlc=1 mods=1 extras=1 other-platform=1 unknown=1 third-party=1 dropped-ue=1 dropped-private=1 dropped-invalid=1 duplicates=1 namespaces=") && $0.contains(" statuses=200x") && $0.contains(" ms=") },
          "the [epic-library] summary line: records, pages, the kinds, what was dropped, the calls, their statuses and the time")
    check(all.contains { $0.hasPrefix("[epic-auth] token exchange status=200 ms=") } && all.contains { $0.hasPrefix("[epic-auth] verify status=200 ms=") }
          && all.contains { $0 == "[epic-auth] signed in account=a1..90 name=1" } && all.contains { $0.hasPrefix("[epic-auth] refresh status=200 ms=") }
          && all.contains { $0 == "[epic-auth] refresh refused; signed out" } && all.contains { $0.hasPrefix("[epic-auth] signed out revoke status=204 ms=") },
          "the [epic-auth] lines: exchange, verify, signed in (masked account), refresh, refusal, sign-out, each with status and timing")
}

@main struct Harness {
    static func main() async {
        installSink()
        await authPhase()
        await transportPhase()
        await libraryPhase()
        logPhase()
        print(failures == 0 ? "EPIC-HARNESS: all passed" : "EPIC-HARNESS: \(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}
