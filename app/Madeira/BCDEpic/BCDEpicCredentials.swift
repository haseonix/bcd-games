// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// What an Epic game's signed-in start asks Epic for (docs/research/open-fork-2026-10-18/epic.md §2.4, §4.4 step
// 4): right before the session, the ownership token (for games whose copy protection asks) and then, last, the
// one-time exchange code, each within ten seconds and with one more try. Written from the design's facts (the
// exchange endpoint's path and its five-minute, single-use code are also in EpicResearch's documentation of
// the account service; the ownership endpoint and the shape of both replies are the design's §2.4 table).
// No code of any other Epic client. Foundation only.
//
// A code or token is in memory from here to the request file (BCDEpicHostFiles) and is never logged: the
// [epic-launch] lines carry a step, a status, a time and a short reason.

enum BCDEpicCredentialsAPI {
    static let ecommerceHost = "ecommerceintegration-public-service-ecomprod02.ol.epicgames.com"

    /// GET the account service's exchange endpoint with the session's token: a code that is good once, for about five minutes.
    static func exchangeRequest(accessToken: String) -> URLRequest {
        var request = URLRequest(url: URL(string: "https://\(BCDEpicAuth.accountHost)/account/api/oauth/exchange")!)
        request.timeoutInterval = 10
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("bearer " + accessToken, forHTTPHeaderField: "Authorization")
        return request
    }

    /// POST the ecommerce service's ownership-token endpoint: Epic's proof, valid for five minutes, that this account owns the item.
    static func ownershipRequest(accessToken: String, accountID: String, namespace: String, catalogItemId: String) -> URLRequest? {
        guard BCDEpicAuth.isAccountID(accountID), BCDEpicCatalog.validNamespace(namespace), BCDEpicCatalog.validCatalogID(catalogItemId),
              let url = URL(string: "https://\(ecommerceHost)/ecommerceintegration/api/public/platforms/EPIC/identities/\(BCDEpicAuth.encode(accountID))/ownershipToken") else { return nil }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 10
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.setValue("bearer " + accessToken, forHTTPHeaderField: "Authorization")
        request.httpBody = BCDEpicAuth.form([("nsCatalogItemId", namespace + ":" + catalogItemId)])
        return request
    }

    /// The exchange reply's code and the seconds it lasts. The code is letters and digits (32 hexadecimal digits in practice).
    static func exchange(from data: Data) -> (code: String, lifetime: TimeInterval)? {
        guard let reply = BCDEpicAuth.jsonObject(data), let code = reply["code"] as? String, BCDEpicAuth.isCode(code) else { return nil }
        var lifetime = 299.0
        if let n = reply["expiresInSeconds"] as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(), n.doubleValue.isFinite { lifetime = min(max(n.doubleValue, 30), 3600) }
        return (code, lifetime)
    }

    /// The ownership reply, as received: a JSON object with a token that starts "egoc1~". The whole reply is what goes into the
    /// token file (some games' checks refuse a bare token), so the bytes are kept exactly (at most 16 KB).
    static func ownershipReply(from data: Data) -> Data? {
        guard data.count <= 16 << 10, let reply = BCDEpicAuth.jsonObject(data), let token = reply["token"] as? String,
              token.hasPrefix("egoc1~"), BCDEpicAuth.isToken(token) else { return nil }
        return data
    }
}

extension BCDEpicAPI {
    /// One call for a start: the session's token (refreshed when it is about to lapse, and once more on a 401), ten seconds a try and
    /// one more try after a short pause when the network or the server failed.
    func sendForStart(step: String, _ make: @Sendable (String) -> URLRequest?) async throws -> Data {
        var attempt = 0
        var refreshed = false
        while true {
            attempt += 1
            let token = try await session.accessToken()
            guard var request = make(token), BCDEpicAPI.trusted(request.url!) else { throw BCDEpicError.badReply("request") }
            request.timeoutInterval = 10
            let started = BCDEpicLog.now()
            do {
                var (status, body) = try await transport.send(request)
                if status == 401, !refreshed {
                    refreshed = true
                    guard var again = make(try await session.refresh().accessToken) else { throw BCDEpicError.badReply("request") }
                    again.timeoutInterval = 10
                    (status, body) = try await transport.send(again)
                }
                BCDEpicLog.write("[epic-launch] \(step) status=\(status) ms=\(BCDEpicLog.millis(since: started))")
                if (200...299).contains(status) {
                    guard body.count <= 64 << 10 else { throw BCDEpicError.badReply("size") }
                    return body
                }
                let failure = BCDEpicAuth.failure(status: status, body: body)
                if case .http = failure, attempt < 2 { try await Task.sleep(nanoseconds: 400_000_000); continue }
                throw failure
            } catch let error as URLError where attempt < 2 && [.timedOut, .networkConnectionLost, .notConnectedToInternet, .cannotConnectToHost, .dnsLookupFailed].contains(error.code) {
                BCDEpicLog.write("[epic-launch] \(step) retry reason=net-\(error.code.rawValue) ms=\(BCDEpicLog.millis(since: started))")
                try await Task.sleep(nanoseconds: 400_000_000)
                continue
            }
        }
    }

    /// What a signed-in start needs from Epic: the account, the ownership token when the game asks for one, then the exchange code, last
    /// (it lasts only minutes). Everything within `deadline` seconds in all.
    func credentials(namespace: String, catalogItemId: String, needsOwnership: Bool, deadline: TimeInterval = 25) async throws -> BCDEpicCredentials {
        do {
            return try await withThrowingTaskGroup(of: BCDEpicCredentials.self) { group in
                group.addTask { try await self.mint(namespace: namespace, catalogItemId: catalogItemId, needsOwnership: needsOwnership) }
                group.addTask {
                    try await Task.sleep(nanoseconds: UInt64(deadline * 1_000_000_000))
                    throw BCDEpicPlayFailure.offline
                }
                defer { group.cancelAll() }
                guard let first = try await group.next() else { throw BCDEpicPlayFailure.reply }
                return first
            }
        } catch {
            throw Self.failure(from: error)
        }
    }

    private func mint(namespace: String, catalogItemId: String, needsOwnership: Bool) async throws -> BCDEpicCredentials {
        _ = try await session.accessToken()   // refreshed first when it is about to lapse: the account below is the live one
        guard let held = await session.current(), let account = held.accountID, BCDEpicAuth.isAccountID(account) else { throw BCDEpicPlayFailure.signedOut }
        var ownership: Data?
        if needsOwnership {
            let body = try await sendForStart(step: "ownership") {
                BCDEpicCredentialsAPI.ownershipRequest(accessToken: $0, accountID: account, namespace: namespace, catalogItemId: catalogItemId)
            }
            guard let reply = BCDEpicCredentialsAPI.ownershipReply(from: body) else { throw BCDEpicPlayFailure.reply }
            ownership = reply
        }
        let body = try await sendForStart(step: "code") { BCDEpicCredentialsAPI.exchangeRequest(accessToken: $0) }
        guard let made = BCDEpicCredentialsAPI.exchange(from: body) else { throw BCDEpicPlayFailure.reply }
        return BCDEpicCredentials(accountID: account, displayName: held.displayName, exchangeCode: made.code, ownership: ownership, made: Date(), codeLifetime: made.lifetime)
    }

    /// An error of the calls above as the failure a start reports.
    static func failure(from error: Error) -> BCDEpicPlayFailure {
        if let error = error as? BCDEpicPlayFailure { return error }
        if error is CancellationError { return .offline }
        if let error = error as? URLError { _ = error; return .offline }
        if let error = error as? BCDEpicError {
            switch error {
            case .signedOut, .signInRefused: return .signedOut
            case .corrective: return .corrective
            case .rejected(let status, _): return [403, 404].contains(status) ? .notOwned : .server(status)
            case .http(let status): return .server(status)
            case .badReply: return .reply
            }
        }
        return .reply
    }
}
