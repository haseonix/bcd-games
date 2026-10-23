// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

/// The question BCD asks when an Epic game's signed-in start can't be made, over the library, as the app shows it (the system's
/// alert through `bcdAsk`). The words are BCDEpicPrompt's (the engine's file is not in the preview); tests/host/check-epic.py
/// holds these strings to the real ones.
/// -BCDTab epic-ask-offline   no connection, and the game can run without Epic: Try again, Play offline, Cancel
/// -BCDTab epic-ask-owned     Epic didn't confirm the game is owned: Try again, Cancel
/// -BCDTab epic-ask-signin    the game needs the sign-in and BCD is signed out: OK
struct BCDEpicAskPreview: View {
    let spec: String
    @ObservedObject var store: BCDStore
    @ObservedObject var focus: BCDFocus
    @State private var ask: BCDAsk?

    private var sample: BCDAsk {
        let title = "Epic sign-in"
        switch spec {
        case "epic-ask-owned":
            return BCDAsk(title: title, message: "Epic didn't confirm that you own Citadel Keeper. Check that this is the account that has it, then try again.",
                          answers: [.init(id: "retry", title: "Try again"), .init(id: "cancel", title: "Cancel", cancel: true)])
        case "epic-ask-signin":
            return BCDAsk(title: title, message: "Citadel Keeper needs your Epic sign-in to start. Sign in to Epic Games in Settings › Accounts, then press Play.",
                          answers: [.init(id: "cancel", title: "OK", cancel: true)])
        default:
            return BCDAsk(title: title, message: "BCD couldn't reach Epic to sign Hollow Lantern in. Check the internet connection. You can also start the game without signing in to Epic.",
                          answers: [.init(id: "retry", title: "Try again"), .init(id: "offline", title: "Play offline"), .init(id: "cancel", title: "Cancel", cancel: true)])
        }
    }

    var body: some View {
        BCDFrontEnd(store: store, tab: .library, focus: focus)
            .onAppear { DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { ask = sample } }
            .bcdAsk($ask) { _ in }
    }
}
