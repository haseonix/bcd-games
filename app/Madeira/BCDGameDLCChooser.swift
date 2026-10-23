// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

// The DLC chooser of a game's page (BCDGameDLC.swift has its state and words): "Choose DLC…" on the question before a first
// install opens it. Every owned DLC with files is a row with a switch, its name and its size; all are on to begin with. Above the
// buttons a line says what the download comes to (the game and the DLC chosen); Install stores exactly the set chosen and starts
// the download, Cancel goes back to the question.
//
// Long lists work: the rows scroll, while the title, Select all / Select none, the total and the buttons stay where they are, so
// Install is always reachable. A controller walks every row and the buttons (ids `game.dlc.row.<App ID>`, `game.dlc.all`,
// `game.dlc.none`, `game.dlc.cancel`, `game.dlc.install`; the layer is `game.dlc.`), and the list scrolls to the row in focus.
// On a phone on its side (little height) the card has two columns: the controls on the left, the rows on the right.

struct BCDDLCChooserView: View {
    let chooser: BCDDLCChooser
    let title: String
    let size: CGSize
    let insets: EdgeInsets
    /// The controller's hints show along the bottom: the card keeps clear of them.
    let hints: Bool
    let toggle: (UInt32) -> Void
    let selectAll: () -> Void
    let selectNone: () -> Void
    let install: () -> Void
    let back: () -> Void

    static let layer = "game.dlc."
    static func rowID(_ id: UInt32) -> String { layer + "row.\(id)" }

    private var sideways: Bool { size.height < 520 && size.width > size.height }
    private var cardWidth: CGFloat { min(sideways ? 780 : 560, size.width - (sideways ? 40 : 32)) }
    private var cardHeight: CGFloat {
        let room = size.height - insets.top - insets.bottom - (hints ? 52 : 0) - (sideways ? 16 : 40)
        return min(sideways ? 400 : 780, max(260, room))
    }

    var body: some View {
        ZStack {
            Color.black.opacity(0.6).ignoresSafeArea().onTapGesture { back() }
            Group {
                if sideways {
                    HStack(alignment: .top, spacing: 16) {
                        controls.frame(width: 250)
                        list
                    }
                } else {
                    VStack(alignment: .leading, spacing: 10) {
                        heading
                        chips
                        list
                        footer
                    }
                }
            }
            .padding(sideways ? 16 : 18)
            .frame(width: cardWidth, height: cardHeight)
            .background(BCDStyle.surfaceRaised, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
            .offset(y: hints ? -26 : 0)
        }
    }

    // MARK: Pieces

    private var heading: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Choose your DLC").font(BCDStyle.heading(20))
            Text(title).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft).lineLimit(1)
        }
    }

    private var chips: some View {
        HStack(spacing: 8) {
            chip("Select all", id: "game.dlc.all", dim: chooser.allOn, run: selectAll)
            chip("Select none", id: "game.dlc.none", dim: chooser.noneOn, run: selectNone)
            Spacer(minLength: 0)
        }
    }

    private func chip(_ text: String, id: String, dim: Bool, run: @escaping () -> Void) -> some View {
        Button(action: run) {
            Text(text).font(BCDStyle.label(14)).foregroundStyle(BCDStyle.lavender)
                .padding(.horizontal, 14).frame(minHeight: 36)
                .background(BCDStyle.surface, in: Capsule())
                .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
                .opacity(dim ? 0.55 : 1)
        }
        .buttonStyle(.plain)
        .bcdFocusable(id, cornerRadius: 18, action: run)
        .id(id)
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(chooser.summary).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
            buttons
        }
    }

    private var buttons: some View {
        HStack(spacing: 10) {
            Button { back() } label: { Text("Cancel").lineLimit(1) }
                .buttonStyle(BCDDLCChooserButton(primary: false))
                .bcdFocusable("game.dlc.cancel", cornerRadius: 22) { back() }
            Button { install() } label: { Text(chooser.installTitle).lineLimit(1).minimumScaleFactor(0.75) }
                .buttonStyle(BCDDLCChooserButton(primary: true))
                .bcdFocusable("game.dlc.install", cornerRadius: 22) { install() }
        }
    }

    /// The controls of the two-column layout.
    private var controls: some View {
        VStack(alignment: .leading, spacing: 10) {
            heading
            chips
            Spacer(minLength: 0)
            footer
        }
    }

    private var list: some View {
        ScrollViewReader { scroller in
            ScrollView(.vertical, showsIndicators: true) {
                VStack(spacing: 8) {
                    ForEach(chooser.rows) { row in rowView(row) }
                }
                // Room for the controller's ring, which is drawn a little outside the row: the list must not clip it.
                .padding(.vertical, 6).padding(.horizontal, 8)
            }
            .modifier(BCDDLCChooserFocusScroll(scroller: scroller))
        }
    }

    private func rowView(_ row: BCDDLCOfferItem) -> some View {
        let on = chooser.isOn(row.id)
        let id = Self.rowID(row.id)
        return Button { toggle(row.id) } label: {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.name).font(BCDStyle.label(15)).lineLimit(2).multilineTextAlignment(.leading)
                    if row.bytes > 0 {
                        Text(BCDFormat.bytes(row.bytes)).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                    }
                }
                Spacer(minLength: 8)
                BCDGameSwitch(on: on)
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
            .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .bcdFocusable(id, cornerRadius: 14) { toggle(row.id) }
        .id(id)
        .accessibilityLabel(row.name + (row.bytes > 0 ? ", " + BCDFormat.bytes(row.bytes) : "") + (on ? ", on" : ", off"))
    }
}

/// Scrolls the list to the row the controller's ring is on.
private struct BCDDLCChooserFocusScroll: ViewModifier {
    let scroller: ScrollViewProxy
    @EnvironmentObject private var focus: BCDFocus

    func body(content: Content) -> some View {
        content.onChange(of: focus.focused) { _, id in
            guard let id, id.hasPrefix(BCDDLCChooserView.layer + "row.") else { return }
            withAnimation(.easeOut(duration: 0.2)) { scroller.scrollTo(id, anchor: .center) }
        }
    }
}

/// The chooser's two buttons: a little lower than the page's, so the list keeps its room on a phone on its side.
private struct BCDDLCChooserButton: ButtonStyle {
    let primary: Bool
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(BCDStyle.label(16))
            .foregroundStyle(primary ? Color.white : BCDStyle.text)
            .frame(maxWidth: .infinity, minHeight: 44)
            .background {
                if primary {
                    Capsule().fill(BCDStyle.glow)
                } else {
                    Capsule().fill(BCDStyle.surface).overlay(Capsule().strokeBorder(BCDStyle.glow, lineWidth: 1.5))
                }
            }
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
    }
}
