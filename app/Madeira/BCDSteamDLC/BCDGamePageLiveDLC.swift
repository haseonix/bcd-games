// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

// The game page's DLC, engine side (BCDGameDLC.swift has the page's rules and words): which Steam games get the section, the
// question before a first install and the switches. Engine-bound like BCDGamePageLive.swift, so the preview build leaves it
// out (it lives in a folder of its own, which the preview build does not read).
//
// The DLC list comes from BCD's own Steam sign-in and license list (SteamDLCService); the downloads from BCD's own depot
// downloader. Valve's client plays no part: it finds what was installed and recorded when BCD Dock starts the game.

extension BCDGamePageLive {
    /// DLC support is on, and BCD's Steam library can look things up: an owned library, signed in.
    private var dlcAvailable: Bool {
        SteamOwnedLibrary.enabled && SteamDLCService.enabled && SteamOwnedLibrary.shared.signedIn
    }

    private func dlcAppID(_ game: BCDGame) -> Int? {
        if case .steam(let id)? = BCDLiveID.parse(game.id) { return id }
        return nil
    }

    /// The page opened: the game's owned DLC is looked up (once a run), so the question before an install and the section
    /// have it by the time they are needed.
    func dlcOpened(_ game: BCDGame) {
        guard dlcAvailable, let id = dlcAppID(game) else { return }
        // An installed game BCD did not download is not BCD's to change.
        if game.state.isInstalled {
            guard let installed = SteamGamesModel.shared.games.first(where: { $0.id == id }),
                  SteamInstallPaths.isManaged(library: installed.library) else { return }
        }
        SteamOwnedLibrary.shared.dlc.load(id)
    }

    /// Changes of the DLC state the page should read again.
    var dlcChanges: AnyPublisher<Void, Never> {
        SteamOwnedLibrary.shared.dlc.changed.map { _ in () }.eraseToAnyPublisher()
    }

    /// A game that is not installed: the question before its first install, when it has owned DLC with files and the player
    /// was not asked yet. While the lookup runs the page's Install waits for it (`dlcCheck`); a lookup that failed or could not
    /// start means no question.
    func dlcOffer(_ game: BCDGame, into d: inout BCDGameDetails) {
        guard dlcAvailable, case .notInstalled = game.state, let id = dlcAppID(game) else { return }
        let service = SteamOwnedLibrary.shared.dlc
        // Asked at every Install of a game that is not installed, even when an earlier, cancelled install stored an answer
        // (device round 3, build 586: an answer from a cancelled install was used again without asking). The choice made
        // now replaces it. Not while its download runs.
        guard SteamOwnedLibrary.shared.downloads[id] == nil else { return }
        guard let report = service.report(id) else {
            d.dlcCheck = service.isLooking(id) ? .checking : .failed
            return
        }
        guard !report.owned.isEmpty else { return }
        d.dlcOffer = BCDDLCOffer(items: report.owned.map {
            BCDDLCOfferItem(id: $0.appID, name: $0.name, bytes: Int64(clamping: $0.downloadBytes))
        })
    }

    /// An installed game BCD downloaded: Options' DLC section.
    func dlcGroup(_ game: BCDGame) -> BCDGameSettingGroup? {
        guard dlcAvailable, let id = dlcAppID(game),
              let installedGame = SteamGamesModel.shared.games.first(where: { $0.id == id }),
              SteamInstallPaths.isManaged(library: installedGame.library) else { return nil }
        let service = SteamOwnedLibrary.shared.dlc
        guard let report = service.report(id), !report.owned.isEmpty else { return nil }
        let installed = service.installed(id), desired = service.desired(id)
        let rows = report.owned.map { item in
            BCDDLCRow(id: item.appID, name: item.name, bytes: Int64(clamping: item.downloadBytes),
                      installed: installed.contains(item.appID), wanted: desired.contains(item.appID))
        }
        return BCDGameDLC.group(BCDDLCState(rows: rows, withoutFiles: report.ownedWithoutContent, notOwned: report.notOwned,
                                            busy: SteamOwnedLibrary.shared.downloads[id] != nil))
    }

    /// A DLC setting of the page. True when the setting was one of the section's.
    func dlcSet(_ setting: String, to value: String, for game: BCDGame) -> Bool {
        guard setting.hasPrefix("dlc."), let id = dlcAppID(game) else { return false }
        let service = SteamOwnedLibrary.shared.dlc
        switch setting {
        case BCDGameDLC.skipID:
            // The question before an install could not be asked (the lookup ran out of time or failed): the game installs alone.
            SteamLog.event("[steam-dlc] question skipped app=\(id) reason=\(value)")
        case BCDGameDLC.chooseID:
            // The question before a first install: the DLC the player chose (a list of App IDs; "all", "none") is kept before the
            // download starts, and the download takes exactly that set.
            if let chosen = BCDGameDLC.chosenSet(value) {
                service.chooseForNewInstall(id, selected: chosen)
            } else {
                service.chooseForNewInstall(id, includeAll: true)
            }
        case BCDGameDLC.allID:
            service.selectAllOwned(id)
            service.apply(id)
        case BCDGameDLC.applyID:
            service.apply(id)
        default:
            if let dlc = BCDGameDLC.dlcID(setting) { service.toggle(id, dlc: dlc, on: value == "1") }
        }
        return true
    }
}
