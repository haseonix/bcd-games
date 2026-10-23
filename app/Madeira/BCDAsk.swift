// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

/// A question with a few answers, shown as the system's alert (a store's start that cannot go on says why and asks what to do:
/// the Epic sign-in's "Try again / Play offline / Cancel" is the first user). The words and the answers' ids come from the
/// source; this file draws them and names no store. The preview app (app/BCDPreview) shows it too.
struct BCDAsk: Equatable, Identifiable {
    struct Answer: Equatable, Identifiable {
        var id: String
        var title: String
        /// Drawn as the alert's cancel button (also what the system's dismissal means).
        var cancel = false
    }

    var title: String
    var message: String
    var answers: [Answer]

    var id: String { title + "\n" + message }
}

extension View {
    /// Shows `ask` as an alert while it is not nil; any answer clears it after `answer` got its id.
    func bcdAsk(_ ask: Binding<BCDAsk?>, answer: @escaping (String) -> Void) -> some View {
        alert(ask.wrappedValue?.title ?? "", isPresented: Binding(get: { ask.wrappedValue != nil }, set: { if !$0 { ask.wrappedValue = nil } }),
              presenting: ask.wrappedValue) { shown in
            ForEach(shown.answers) { item in
                Button(item.title, role: item.cancel ? .cancel : nil) { answer(item.id) }
            }
        } message: { shown in
            Text(shown.message)
        }
    }
}
