// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// A Steam game's DLC on its page (docs/BCD-UI-ARCHITECTURE.md; docs/STEAM_LIBRARY.md, "DLC"): the question
// before a first install and Options' DLC section, in BCD's words. No views and no engine types: the live backend
// (BCDSteamDLC/BCDGamePageLiveDLC.swift) fills the state in, the page's own list and question draw it, and the preview
// app builds the same section from sample rows (tests/host/check-steam-dlc.py compiles this file with BCDGamePage.swift's rules).
//
// Only DLC the player owns is listed, each with its size and a switch. The DLC the player does not own is a count and
// nothing else: BCD never offers to buy anything.

/// One owned DLC with files, as the question's chooser lists it.
struct BCDDLCOfferItem: Equatable, Identifiable {
    var id: UInt32
    var name: String
    /// Its download, about; 0 when Steam sent no size.
    var bytes: Int64
}

/// What the question before a first install says: the owned DLC the game has content for.
struct BCDDLCOffer: Equatable {
    /// The DLC, in the order the page lists them (by name).
    var items: [BCDDLCOfferItem]

    var count: Int { items.count }
    /// The compressed download of all of it; 0 when Steam sent no size.
    var bytes: Int64 { items.reduce(0) { $0 + $1.bytes } }
    var names: [String] { items.map(\.name) }
}

/// The chooser the question's "Choose DLC…" opens: every owned DLC with files as a switch, all on to begin with, and what
/// the download comes to. Install stores exactly the chosen set for the new install; none chosen is the game alone.
struct BCDDLCChooser: Equatable {
    var place: BCDInstallPlace
    var rows: [BCDDLCOfferItem]
    var selected: Set<UInt32>
    /// The game's own download; 0 when unknown.
    var gameBytes: Int64

    init(place: BCDInstallPlace, offer: BCDDLCOffer, gameBytes: Int64) {
        self.place = place
        rows = offer.items
        selected = Set(offer.items.map(\.id))
        self.gameBytes = gameBytes
    }

    func isOn(_ id: UInt32) -> Bool { selected.contains(id) }
    mutating func toggle(_ id: UInt32) {
        guard rows.contains(where: { $0.id == id }) else { return }
        if selected.contains(id) { selected.remove(id) } else { selected.insert(id) }
    }
    mutating func selectAll() { selected = Set(rows.map(\.id)) }
    mutating func selectNone() { selected = [] }

    var chosen: [BCDDLCOfferItem] { rows.filter { selected.contains($0.id) } }
    var chosenBytes: Int64 { chosen.reduce(0) { $0 + $1.bytes } }
    var totalBytes: Int64 { gameBytes + chosenBytes }
    var allOn: Bool { selected.count == rows.count }
    var noneOn: Bool { selected.isEmpty }

    /// The line over Install: the game, the DLC chosen and the total. A size and its unit never split across two lines.
    var summary: String {
        func fixed(_ bytes: Int64) -> String { BCDFormat.bytes(bytes).replacingOccurrences(of: " ", with: "\u{00A0}") }
        let dlc = "\(selected.count) of \(rows.count) DLC" + (chosenBytes > 0 ? " \(fixed(chosenBytes))" : "")
        guard gameBytes > 0 else { return "Download: " + dlc }
        guard !selected.isEmpty else { return "Download: the game \(fixed(gameBytes)), no DLC" }
        return "Download: the game \(fixed(gameBytes)) + \(dlc) =\u{00A0}\(fixed(totalBytes))"
    }

    var installTitle: String { selected.isEmpty ? "Install the game only" : "Install" }
}

/// Where the lookup of a not-installed game's DLC stands, for the page's Install: it waits while the lookup runs, so the question
/// before the first install is not missed by a tap that comes first.
enum BCDDLCCheck: Equatable {
    /// Nothing to wait for: the lookup is done (the offer may be there), or the game is not asked about DLC.
    case none
    /// Steam is being asked.
    case checking
    /// The lookup failed or could not start: the game installs alone.
    case failed
}

/// One owned DLC of an installed game.
struct BCDDLCRow: Equatable {
    var id: UInt32
    var name: String
    /// Its download, about; 0 when Steam sent no size.
    var bytes: Int64
    var installed: Bool
    /// What the switch says: the player wants it installed.
    var wanted: Bool
}

/// The section's data.
struct BCDDLCState: Equatable {
    var rows: [BCDDLCRow]
    /// Owned DLC with no file for a Windows install.
    var withoutFiles = 0
    /// DLC the game has that the account does not own; shown as a number.
    var notOwned = 0
    /// The game's download runs: the switches wait for it.
    var busy = false
}

enum BCDGameDLC {
    /// The section's settings are `dlc.<App ID>` (a switch), `dlc.all` and `dlc.apply` (buttons).
    static let allID = "dlc.all", applyID = "dlc.apply", chooseID = "dlc.choice"
    /// The question was skipped (the value says why: "timeout" or "lookup-failed"); the live backend logs it.
    static let skipID = "dlc.skipped"
    /// How long Install waits for the lookup before it installs the game alone.
    static let checkSeconds: Double = 15

    /// The card shown while Install waits for the lookup. "Game only" installs at once; Cancel installs nothing.
    static func checkingQuestion(title: String) -> BCDGameQuestion {
        BCDGameQuestion(title: "Checking your DLC…",
                        message: "BCD is asking Steam which DLC you own for \(title). If you own some, you can choose them in a moment.",
                        confirm: "Game only", destructive: false)
    }

    static func settingID(_ dlc: UInt32) -> String { "dlc.\(dlc)" }

    /// What the page tells the backend about the DLC chosen for a new install: "none" for the game alone, else the App IDs,
    /// comma-separated ("all" is also understood: every owned DLC).
    static func chooseValue(_ chosen: [UInt32]) -> String {
        chosen.isEmpty ? "none" : chosen.sorted().map(String.init).joined(separator: ",")
    }

    /// The same, read back: nil for "all" (every owned DLC), else the App IDs (none for "none").
    static func chosenSet(_ value: String) -> Set<UInt32>? {
        if value == "all" { return nil }
        return Set(value.split(separator: ",").compactMap { UInt32($0.trimmingCharacters(in: .whitespaces)) })
    }

    static func dlcID(_ setting: String) -> UInt32? {
        guard setting.hasPrefix("dlc."), let id = UInt32(setting.dropFirst(4)) else { return nil }
        return id
    }

    private static func count(_ n: Int, _ word: String = "DLC") -> String { "\(n) \(word)" }

    private static func size(_ bytes: Int64) -> String? { bytes > 0 ? BCDFormat.bytes(bytes) : nil }

    /// "A, B and C" (at most three names, then "and N more").
    private static func list(_ names: [String]) -> String {
        let shown = names.prefix(3).map { "\u{201C}\($0)\u{201D}" }
        guard !shown.isEmpty else { return "" }
        let rest = names.count - shown.count
        if rest > 0 { return shown.joined(separator: ", ") + " and \(rest) more" }
        if shown.count == 1 { return shown[0] }
        return shown.dropLast().joined(separator: ", ") + " and " + shown[shown.count - 1]
    }

    /// The question before a first install of a game that has owned DLC. "Choose DLC…" opens the chooser (every owned DLC as a
    /// switch, all on); "Game only" installs the game alone. Asked once for a game.
    static func offerQuestion(_ offer: BCDDLCOffer, title: String) -> BCDGameQuestion {
        let more = size(offer.bytes).map { " (\($0) in all)" } ?? ""
        let what = offer.count == 1 ? "1 DLC" : "\(offer.count) DLC"
        let names = list(offer.names)
        return BCDGameQuestion(
            title: "Install your DLC too?",
            message: "You own \(what) for \(title)\(names.isEmpty ? "" : ": \(names)")\(more). Steam installs owned DLC with the game; "
                + "you choose which of them BCD installs with it. You can add or remove DLC later in Options › DLC.",
            confirm: "Choose DLC…", destructive: false, alternative: "Game only")
    }

    /// What Apply does, for its row: "Download 2 DLC · 1.2 GB", "Remove 1 DLC", or both.
    static func applyTitle(_ state: BCDDLCState) -> String? {
        let add = state.rows.filter { $0.wanted && !$0.installed }
        let remove = state.rows.filter { !$0.wanted && $0.installed }
        guard !add.isEmpty || !remove.isEmpty else { return nil }
        var parts: [String] = []
        if !add.isEmpty {
            let bytes = add.reduce(Int64(0)) { $0 + $1.bytes }
            parts.append("Download \(count(add.count))" + (size(bytes).map { " · \($0)" } ?? ""))
        }
        if !remove.isEmpty { parts.append("Remove \(count(remove.count))") }
        return parts.joined(separator: ", ")
    }

    /// The question Apply asks: the size of a download, the files a removal deletes.
    static func applyQuestion(_ state: BCDDLCState) -> BCDGameQuestion? {
        let add = state.rows.filter { $0.wanted && !$0.installed }
        let remove = state.rows.filter { !$0.wanted && $0.installed }
        guard !add.isEmpty || !remove.isEmpty else { return nil }
        var lines: [String] = []
        if !add.isEmpty {
            let bytes = add.reduce(Int64(0)) { $0 + $1.bytes }
            lines.append("BCD downloads \(count(add.count))" + (size(bytes).map { " (\($0))" } ?? "")
                         + " and checks the game's files on the way.")
        }
        if !remove.isEmpty {
            let bytes = remove.reduce(Int64(0)) { $0 + $1.bytes }
            lines.append("BCD deletes the files of \(count(remove.count))" + (size(bytes).map { " (about \($0))" } ?? "")
                         + " from this device. Saves kept outside the game's folder stay.")
        }
        return BCDGameQuestion(title: remove.isEmpty ? "Download the DLC?" : "Change the installed DLC?",
                               message: lines.joined(separator: " "), confirm: remove.isEmpty ? "Download" : "Apply",
                               destructive: !remove.isEmpty)
    }

    /// Options' DLC section, or nil when the account owns no DLC with files for the game.
    static func group(_ state: BCDDLCState) -> BCDGameSettingGroup? {
        guard !state.rows.isEmpty else { return nil }
        let lock = state.busy ? "A download of this game is running. Change its DLC when it has finished." : nil
        var settings: [BCDGameSetting] = state.rows.map { row in
            var parts: [String] = []
            if let bytes = size(row.bytes) { parts.append(bytes) }
            switch (row.installed, row.wanted) {
            case (true, true): parts.append("Installed")
            case (true, false): parts.append("Will be removed")
            case (false, true): parts.append("Will be downloaded")
            case (false, false): parts.append("Not installed")
            }
            return BCDGameSetting(id: settingID(row.id), title: row.name, kind: .toggle, value: row.wanted ? "1" : "0",
                                  defaultValue: row.installed ? "1" : "0", note: parts.joined(separator: " · "), locked: lock)
        }
        settings[0].heading = "Your DLC"
        let missing = state.rows.filter { !$0.installed }
        if !missing.isEmpty {
            let bytes = missing.reduce(Int64(0)) { $0 + $1.bytes }
            settings.append(BCDGameSetting(
                id: allID, title: "Install all your DLC" + (size(bytes).map { " · \($0)" } ?? ""), kind: .action,
                note: "Downloads every DLC you own for this game, at once.", locked: lock,
                question: BCDGameQuestion(title: "Install all your DLC?",
                                          message: "BCD downloads \(count(missing.count))" + (size(bytes).map { " (\($0))" } ?? "")
                                              + " and checks the game's files on the way.",
                                          confirm: "Download", destructive: false),
                heading: "Changes"))
        }
        if let title = applyTitle(state) {
            settings.append(BCDGameSetting(id: applyID, title: title, kind: .action,
                                           note: "Puts the switches above into effect.", locked: lock,
                                           question: applyQuestion(state), heading: missing.isEmpty ? "Changes" : nil))
        }
        if state.notOwned > 0 {
            settings.append(BCDGameSetting(id: "dlc.notowned", title: "Other DLC", kind: .info,
                                           value: state.notOwned == 1 ? "1 more DLC for this game isn't in your library."
                                                                      : "\(state.notOwned) more DLC for this game aren't in your library.",
                                           heading: "Not in your library"))
        }
        if state.withoutFiles > 0 {
            settings.append(BCDGameSetting(id: "dlc.nofiles", title: "No files to download", kind: .info,
                                           value: state.withoutFiles == 1 ? "1 DLC you own adds no files for Windows."
                                                                          : "\(state.withoutFiles) DLC you own add no files for Windows."))
        }
        let installed = state.rows.filter(\.installed).count
        var headline = installed == 0 ? "None installed" : "\(installed) of \(state.rows.count) installed"
        if state.rows.contains(where: { $0.wanted != $0.installed }) { headline += " · changes waiting" }
        return BCDGameSettingGroup(id: "dlc", title: "DLC", symbol: "puzzlepiece.extension", settings: settings, headline: headline)
    }
}
