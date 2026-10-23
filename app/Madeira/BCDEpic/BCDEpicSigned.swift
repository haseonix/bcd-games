// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// What an Epic game's signed-in start is made of (docs/research/open-fork-2026-10-18/epic.md §2.4, §4.4, §5 M2):
// the arguments Epic's launcher gives a game it starts (an exchange code, the account's ID and name, the
// sandbox, and a file with an ownership token for games whose copy protection asks), when a start is signed in
// and when it is not, why a sign-in can fail and what the player is told. Written from the design's
// fact tables (the launch-argument table of §2.4 and the facts of its §6), with no code of any other Epic
// client. Foundation only (the host checks compile it).
//
// This file is one of two that may name an exchange code, an account ID, a display name or an ownership
// token (the other is BCDEpicCredentials.swift, which fetches them): tests/host/check-epic.py pins both.
// Nothing here logs or stores a value; the credentials print as <redacted>.

/// What Epic gave one start: an exchange code (single use, about five minutes), the account, and (for games that
/// ask) the ownership token's whole reply. Held in memory only until the start has been written.
struct BCDEpicCredentials: CustomStringConvertible, CustomDebugStringConvertible {
    var accountID: String
    var displayName: String?
    var exchangeCode: String
    /// Epic's whole ownership-token reply as received (JSON with the token in it).
    var ownership: Data?
    var made: Date
    /// How long Epic says the code lasts (seconds).
    var codeLifetime: TimeInterval

    var description: String { "BCDEpicCredentials(<redacted>)" }
    var debugDescription: String { description }

    /// A start must happen within this long of the code being made (Epic's own lifetime, less a safety margin; at most four minutes).
    static let ticketLifetime: TimeInterval = 240

    func valid(now: Date) -> Bool {
        let limit = min(Self.ticketLifetime, max(codeLifetime - 60, 30))
        return now.timeIntervalSince(made) >= 0 && now.timeIntervalSince(made) < limit
    }
}

/// Why a signed-in start could not be made, in words for the player and a short reason for the log.
enum BCDEpicPlayFailure: Error, Equatable {
    /// No connection, or Epic did not answer in time.
    case offline
    /// Epic no longer accepts the stored sign-in (or there is none, and the game needs one).
    case signedOut
    /// Epic wants the player to finish a step on its website.
    case corrective
    /// Epic did not confirm the player owns the game.
    case notOwned
    case server(Int)
    /// Epic's reply was not what BCD expects.
    case reply

    var logReason: String {
        switch self {
        case .offline: return "offline"
        case .signedOut: return "signed-out"
        case .corrective: return "corrective"
        case .notOwned: return "not-owned"
        case .server(let status): return "http-\(status)"
        case .reply: return "reply"
        }
    }

    /// Worth another try right now (the rest need the player to do something first).
    var canRetry: Bool {
        switch self {
        case .offline, .notOwned, .server, .reply: return true
        case .signedOut, .corrective: return false
        }
    }
}

/// The question BCD asks when a signed-in start cannot be made.
struct BCDEpicPrompt: Equatable {
    var title: String
    var message: String
    var offersRetry: Bool
    /// "Play offline" starts the game with Epic's public arguments only (no sign-in): offered only for a game that can run without one.
    var offersOffline: Bool

    static let title = "Epic sign-in"

    struct Answer: Equatable {
        var id: String
        var title: String
        /// The alert's cancel button (also what dismissing it means).
        var cancel = false
    }

    /// The buttons, in order: Try again and Play offline when they are offered, then Cancel (OK when nothing else is offered).
    var answers: [Answer] {
        var list: [Answer] = []
        if offersRetry { list.append(Answer(id: "retry", title: "Try again")) }
        if offersOffline { list.append(Answer(id: "offline", title: "Play offline")) }
        list.append(Answer(id: "cancel", title: offersRetry || offersOffline ? "Cancel" : "OK", cancel: true))
        return list
    }

    static func make(for failure: BCDEpicPlayFailure, offlineAllowed: Bool, game: String) -> BCDEpicPrompt {
        let first: String
        switch failure {
        case .offline: first = "BCD couldn't reach Epic to sign \(game) in. Check the internet connection."
        case .signedOut: first = "Epic no longer accepts your sign-in. Sign in to Epic Games again in Settings › Accounts, then press Play."
        case .corrective: first = "Epic wants you to finish a step on its website (an account or policy question) before it lets BCD sign you in. Do that, then press Play."
        case .notOwned: first = "Epic didn't confirm that you own \(game). Check that this is the account that has it, then try again."
        case .server(let status): first = "Epic's servers answered with an error (\(status)). Try again in a moment."
        case .reply: first = "Epic's answer wasn't what BCD expects (it may have changed). Try again later."
        }
        let message = offlineAllowed ? first + " You can also start the game without signing in to Epic." : first
        return BCDEpicPrompt(title: title, message: message, offersRetry: failure.canRetry, offersOffline: offlineAllowed)
    }

    /// A game that needs Epic's sign-in, started while BCD is signed out of Epic.
    static func needsSignIn(game: String) -> BCDEpicPrompt {
        BCDEpicPrompt(title: title, message: "\(game) needs your Epic sign-in to start. Sign in to Epic Games in Settings › Accounts, then press Play.",
                      offersRetry: false, offersOffline: false)
    }
}

/// When a start is signed in, and the words of its arguments.
enum BCDEpicSignedLaunch {
    // MARK: The decision

    struct Facts: Equatable {
        /// env.BCD_EPIC_SIGNIN: the game's own line, else bcd.cfg's, else on.
        var signInSwitch = true
        /// env.BCD_EPIC_HOST: 0 starts the game directly with the public arguments only.
        var hostSwitch = true
        /// BCD Host is in the app bundle.
        var hostAvailable = true
        var signedIn = true
        /// The program is a batch file, the Desktop entry, or not a Windows program: the host starts none of these.
        var hostCannotStart = false
        /// The game asks Epic for an ownership token, or is marked as needing to be online.
        var needsOnline = false
        /// The player chose "Play offline" for this start.
        var offlineChosen = false
    }

    enum Kind: Equatable {
        /// The code (and token) are fetched and the game starts through BCD Host.
        case signed
        /// The game starts with Epic's public arguments only; the reason is a short word for the log.
        case publicOnly(String)
        /// The game cannot start: it needs a sign-in BCD does not have.
        case needsSignIn
    }

    static func decide(_ facts: Facts) -> Kind {
        if !facts.signInSwitch { return .publicOnly("switch-off") }
        if !facts.hostSwitch { return .publicOnly("host-off") }
        if !facts.hostAvailable { return .publicOnly("no-host") }
        if facts.hostCannotStart { return .publicOnly("no-host-start") }
        if facts.offlineChosen { return .publicOnly("offline") }
        if !facts.signedIn { return facts.needsOnline ? .needsSignIn : .publicOnly("signed-out") }
        return .signed
    }

    /// A game can start without Epic's sign-in when Epic does not say it must be online and it asks for no ownership token.
    static func offlineAllowed(canRunOffline: Bool?, ownershipToken: Bool) -> Bool { canRunOffline != false && !ownershipToken }

    // MARK: The arguments

    /// A display name for a command line: no quote, no control character and no trailing backslash (it would escape the closing quote),
    /// at most 64 characters; nil when nothing is left.
    static func cleanName(_ text: String?) -> String? {
        guard let text else { return nil }
        var scalars = text.unicodeScalars.filter { $0.value >= 0x20 && $0.value != 0x7F && $0 != "\"" }
        while scalars.last == "\\" || scalars.last == " " { scalars.removeLast() }
        while scalars.first == " " { scalars.removeFirst() }
        let name = String(String.UnicodeScalarView(scalars)).prefix(64)
        return name.isEmpty ? nil : String(name)
    }

    /// Epic's own words on a command line, which a player's own text must not repeat (the launcher's set is the one that counts).
    static let epicKeys: Set<String> = ["-auth_login", "-auth_password", "-auth_type", "-epicapp", "-epicenv", "-epicportal", "-epicusername",
                                        "-epicuserid", "-epiclocale", "-epicsandboxid", "-epicdeploymentid", "-epicovt"]

    /// `text` without any of Epic's own arguments (the player's other arguments stay, spelled as written).
    static func withoutEpicArguments(_ text: String) -> String {
        var tokens: [String] = []
        var current = "", quoted = false, any = false
        for ch in text {
            if ch == "\"" { quoted.toggle(); current.append(ch); any = true; continue }
            if (ch == " " || ch == "\t") && !quoted {
                if any { tokens.append(current) }
                current = ""; any = false
            } else { current.append(ch); any = true }
        }
        if any { tokens.append(current) }
        return tokens.filter { token in
            let key = token.split(separator: "=", maxSplits: 1).first.map { String($0).lowercased() } ?? token.lowercased()
            return !epicKeys.contains(key)
        }.joined(separator: " ")
    }

    /// The arguments a signed-in start adds after the game's own: Epic's launcher set, in its order. nil when a value is not
    /// one BCD would put on a command line (a code or ID of an odd shape).
    static func epicArguments(app: String, namespace: String, deploymentID: String?, credentials: BCDEpicCredentials,
                              ovtWindowsPath: String?, preferredLanguages: [String]) -> String? {
        guard BCDEpicAuth.isCode(credentials.exchangeCode), BCDEpicAuth.isAccountID(credentials.accountID),
              BCDEpicCatalog.validAppName(app), BCDEpicCatalog.validNamespace(namespace) else { return nil }
        var list = ["-AUTH_LOGIN=unused", "-AUTH_PASSWORD=" + credentials.exchangeCode, "-AUTH_TYPE=exchangecode",
                    "-epicapp=" + app, "-epicenv=Prod"]
        if let ovtWindowsPath {
            guard ovtWindowsPath.hasPrefix(BCDEpicHostFiles.windowsFolder + "\\"), !ovtWindowsPath.contains("\""), !ovtWindowsPath.contains(" ") else { return nil }
            list.append("-epicovt=" + ovtWindowsPath)
        }
        list.append("-EpicPortal")
        if let name = cleanName(credentials.displayName) {
            list.append("-epicusername=" + (name.contains(" ") ? "\"" + name + "\"" : name))
        }
        list.append("-epicuserid=" + credentials.accountID)
        list.append("-epiclocale=" + BCDEpicLaunch.locale(preferred: preferredLanguages))
        list.append("-epicsandboxid=" + namespace)
        if let deploymentID, BCDEpicLaunch.validDeploymentID(deploymentID) { list.append("-epicdeploymentid=" + deploymentID) }
        return list.joined(separator: " ")
    }

    /// The whole command line after the program: the game's own arguments (BCD's, from the install record, or the player's own text
    /// with Epic's words taken out of it), then the signed set.
    static func tail(gameArguments: String, epic: String) -> String {
        let game = gameArguments.trimmingCharacters(in: .whitespaces)
        return game.isEmpty ? epic : game + " " + epic
    }

    // MARK: Looking for what must not be left behind

    /// True when `line` carries one of the signed arguments with a value (not a masked one): for the host checks of the
    /// log masks and for the in-app audit.
    static func carriesSecret(_ line: String) -> Bool {
        let pattern = #"(?i)(-AUTH_PASSWORD=|-epicuserid=)(?![*<])[^\s"']"#
        return line.range(of: pattern, options: .regularExpression) != nil
    }
}
