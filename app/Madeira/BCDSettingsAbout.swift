// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

// Settings › About: the version and build, the credits and the licence notice, and the licence
// texts that ship inside the app. The credits and the notice are the app's legal notices
// (GPL-3.0 section 0, LICENSE-BCD.md section 4(b)): the live backend shows them through the
// very views BCD's legal notices have always used, word for word, never reworded here.

/// A licence text that ships inside the app.
struct BCDLicenceText: Identifiable, Hashable {
    var id: String          // where it is
    var title: String
}

@MainActor protocol BCDAboutBackend: ObservableObject {
    var version: String { get }
    var build: String { get }
    /// The build's own stamp (its round and time), when the build wrote one.
    var stamp: String? { get }
    /// The credits, BCD first, then each contributor, then the licence notice.
    func credits() -> AnyView
    var licences: [BCDLicenceText] { get }
    func text(of licence: BCDLicenceText) -> String
}

struct BCDAboutSettings<Backend: BCDAboutBackend>: View {
    @ObservedObject var backend: Backend
    @EnvironmentObject private var model: BCDSettingsModel
    @Environment(\.bcdLayout) private var layout

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 16) {
                BCDLogoMark(size: layout == .pad ? 72 : 60)
                VStack(alignment: .leading, spacing: 4) {
                    Text("BCD").font(BCDStyle.title(layout == .pad ? 32 : 26))
                    // BCD is in alpha (owner, 2026-10-21): said next to the version, as on GitHub's releases.
                    Text("Version \(backend.version) (\(backend.build)) · alpha").font(BCDStyle.label(15)).monospacedDigit()
                    if let stamp = backend.stamp, !stamp.isEmpty {
                        Text(stamp).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                    }
                }
                Spacer(minLength: 0)
            }
            .bcdCard(padding: 16)
            .accessibilityElement(children: .combine)
            BCDSettingsGroup {
                BCDSettingsActionRow(id: BCDSettingsID.control(.about, "updates"), title: "Updates",
                                     note: "Whether a newer BCD is out, and checking by itself.", symbol: "arrow.down.circle") {
                    model.go(to: .updates)
                }
            }
            VStack(alignment: .leading, spacing: 8) {
                BCDEyebrow(text: "Credits").padding(.leading, 4)
                backend.credits()
                    .foregroundStyle(BCDStyle.text)
                    .tint(BCDStyle.lavender)
                    .bcdCard(padding: 16)
            }
            if !backend.licences.isEmpty {
                licenceTexts
            }
            BCDSettingsGroup(footer: "Adds Developer to the list: the Windows desktop, BCD's log and a controller check.") {
                BCDSettingsToggleRow(id: BCDSettingsID.control(.about, "developer"), title: "Developer options",
                                     isOn: model.developerShown) { model.showDeveloper($0) }
            }
        }
    }

    private var licenceTexts: some View {
        BCDSettingsGroup(title: "License texts",
                         footer: "The licenses of BCD and of the projects it is built on, as they ship inside the app.") {
            ForEach(backend.licences) { licence in
                BCDSettingsActionRow(id: BCDSettingsID.control(.about, "licence.\(licence.id)"), title: licence.title,
                                     symbol: "doc.text") {
                    model.read(licence.title, text: backend.text(of: licence))
                }
            }
        }
    }
}

/// One credit line inside About, reachable by the controller: A opens `link`.
struct BCDCreditLink<Content: View>: View {
    let id: String
    let link: URL?
    @ViewBuilder var content: Content
    @Environment(\.openURL) private var openURL

    var body: some View {
        content
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .bcdSettingsControl(BCDSettingsID.control(.about, id), cornerRadius: 10) {
                if let link { openURL(link) }
            }
    }
}
