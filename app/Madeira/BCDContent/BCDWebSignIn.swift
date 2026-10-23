// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import UIKit
import WebKit

/// A store's own sign-in page in a sheet, shared by the stores (GOG and Epic Games).
///
/// - The store's page runs in a web view with WebKit's own user agent (a custom one fails GOG's
///   Cloudflare check; a store's page may ask for one: `Page.userAgent`, nil for GOG) and a data
///   store that lives only as long as the sheet: nothing of the sign-in stays on the device but the
///   tokens the store's code keeps.
/// - Sign-in providers that open a window (Steam, Discord, Xbox) open in a child view.
/// - The store's matcher looks at every address the page goes to; when it finds the sign-in's code
///   the navigation is stopped, the sheet closes and the code goes to `completion` (never logged).
/// - A store whose sign-in ends on a page that shows the code as text (Epic Games: a JSON text) says
///   which addresses may be read (`readsPage`) and what the text means (`matchPage`); the page's text
///   is read for those addresses only, never for the store's login pages.
/// - "Use Safari" is the way around a page the web view cannot pass (Google refuses embedded web
///   views; a challenge that loops): the page opens in Safari, and the address it ends on (or the
///   text it shows, `matchPasted`) is pasted back here.
/// - Upright is allowed while the sheet is up (password managers' sheets need it).
@MainActor final class BCDWebSignIn: UIViewController, WKNavigationDelegate, WKUIDelegate, UITextFieldDelegate {
    struct Page {
        var title: String
        var start: URL
        /// The sign-in's code in an address the page went to, or nil for any other address.
        var match: (URL) -> String?
        /// What to tell someone pasting the address from Safari.
        var pasteHint: String
        /// The addresses whose page text may be read once they finish loading (nil: none).
        var readsPage: ((URL) -> Bool)? = nil
        /// What the text of such a page means.
        var matchPage: ((URL, String) -> PageResult)? = nil
        /// What a pasted text is, when it is more than an address (nil: an address `match` reads).
        var matchPasted: ((String) -> String?)? = nil
        var pastePlaceholder = "Paste the address here"
        var pasteProblem = "That isn't the address the sign-in ends on. Copy the whole address from Safari's address bar after signing in."
        /// A user agent for the page, when the store needs one (nil: WebKit's own).
        var userAgent: String? = nil
    }

    /// What a page's text came to: the sign-in's code, another page to go on to, or nothing.
    enum PageResult {
        case code(String)
        case load(URL)
        case keepWaiting
    }

    private let page: Page
    private var completion: ((String?) -> Void)?
    private var webView: WKWebView?
    private var popups: [WKWebView] = []
    private let pasteField = UITextField()
    private let pasteNote = UILabel()
    private var pastePanel: UIView?

    /// Shows the sheet over whatever is on screen. `completion` gets the code, or nil if the person
    /// closed the sheet.
    static func present(_ page: Page, completion: @escaping (String?) -> Void) {
        guard let top = topController() else { completion(nil); return }
        let sheet = BCDWebSignIn(page: page, completion: completion)
        let navigation = UINavigationController(rootViewController: sheet)
        navigation.modalPresentationStyle = UIDevice.current.userInterfaceIdiom == .pad ? .formSheet : .fullScreen
        navigation.isModalInPresentation = true
        top.present(navigation, animated: true)
    }

    static func topController() -> UIViewController? {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let scene = scenes.first { $0.activationState == .foregroundActive } ?? scenes.first
        var top = scene?.keyWindow?.rootViewController
        while let presented = top?.presentedViewController, !presented.isBeingDismissed { top = presented }
        return top
    }

    private init(page: Page, completion: @escaping (String?) -> Void) {
        self.page = page
        self.completion = completion
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) { nil }

    override var supportedInterfaceOrientations: UIInterfaceOrientationMask { .allButUpsideDown }

    override func viewDidLoad() {
        super.viewDidLoad()
        title = page.title
        view.backgroundColor = .systemBackground
        // Cancel closes a provider's window first, then the sheet.
        navigationItem.leftBarButtonItem = UIBarButtonItem(systemItem: .cancel, primaryAction: UIAction { [weak self] _ in
            guard let self else { return }
            if let popup = self.popups.last { self.webViewDidClose(popup) } else { self.finish(nil) }
        })
        navigationItem.rightBarButtonItem = UIBarButtonItem(title: "Use Safari", primaryAction: UIAction { [weak self] _ in
            self?.showPaste()
        })
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        let web = WKWebView(frame: view.bounds, configuration: configuration)
        web.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        web.navigationDelegate = self
        web.uiDelegate = self
        if let agent = page.userAgent, !agent.isEmpty { web.customUserAgent = agent }
        view.addSubview(web)
        webView = web
        web.load(URLRequest(url: page.start))
    }

    // MARK: The page

    private func check(_ url: URL?) -> Bool {
        guard let url, let code = page.match(url) else { return false }
        finish(code)
        return true
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        decisionHandler(check(action.request.url) ? .cancel : .allow)
    }

    func webView(_ webView: WKWebView, didReceiveServerRedirectForProvisionalNavigation navigation: WKNavigation!) {
        if check(webView.url) { webView.stopLoading() }
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        if check(webView.url) { webView.stopLoading() }
    }

    /// A finished page the store reads the text of (Epic's redirect page): the text goes to the store's
    /// matcher, which finds the code, or sends the sheet on to another page.
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard completion != nil, let read = page.matchPage, let url = webView.url, page.readsPage?(url) == true else { return }
        webView.evaluateJavaScript("document.body ? document.body.innerText.slice(0, 65536) : ''") { [weak self] value, _ in
            guard let self, self.completion != nil, let text = value as? String else { return }
            switch read(url, text) {
            case .code(let code): self.finish(code)
            case .load(let next): webView.load(URLRequest(url: next))
            case .keepWaiting: break
            }
        }
    }

    /// A provider's window (window.open): a child view over the page, closed when it closes.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        let child = WKWebView(frame: view.bounds, configuration: configuration)
        child.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        child.navigationDelegate = self
        child.uiDelegate = self
        view.addSubview(child)
        popups.append(child)
        return child
    }

    func webViewDidClose(_ webView: WKWebView) {
        guard let i = popups.firstIndex(of: webView) else { return }
        popups.remove(at: i)
        webView.removeFromSuperview()
    }

    // MARK: Safari, then paste

    private func showPaste() {
        UIApplication.shared.open(page.start)
        guard pastePanel == nil else { return }
        let panel = UIStackView()
        panel.axis = .vertical
        panel.spacing = 12
        panel.isLayoutMarginsRelativeArrangement = true
        panel.directionalLayoutMargins = NSDirectionalEdgeInsets(top: 16, leading: 16, bottom: 16, trailing: 16)
        panel.backgroundColor = .secondarySystemBackground
        let note = UILabel()
        note.numberOfLines = 0
        note.font = .preferredFont(forTextStyle: .subheadline)
        note.text = page.pasteHint
        pasteField.placeholder = page.pastePlaceholder
        pasteField.borderStyle = .roundedRect
        pasteField.autocapitalizationType = .none
        pasteField.autocorrectionType = .no
        pasteField.keyboardType = page.matchPasted == nil ? .URL : .asciiCapable
        pasteField.returnKeyType = .done
        pasteField.delegate = self
        pasteNote.numberOfLines = 0
        pasteNote.font = .preferredFont(forTextStyle: .footnote)
        pasteNote.textColor = .systemRed
        let button = UIButton(configuration: .filled(), primaryAction: UIAction(title: "Continue") { [weak self] _ in
            self?.usePasted()
        })
        [note, pasteField, pasteNote, button].forEach(panel.addArrangedSubview)
        panel.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(panel)
        NSLayoutConstraint.activate([
            panel.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor),
            panel.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor),
            panel.bottomAnchor.constraint(equalTo: view.keyboardLayoutGuide.topAnchor),
        ])
        pastePanel = panel
    }

    func textFieldShouldReturn(_ textField: UITextField) -> Bool {
        usePasted()
        return true
    }

    private func usePasted() {
        let text = (pasteField.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if let matchPasted = page.matchPasted {
            if let code = matchPasted(text) { finish(code) } else { pasteNote.text = page.pasteProblem }
        } else if !check(URL(string: text)) {
            pasteNote.text = page.pasteProblem
        }
    }

    // MARK: Done

    private func finish(_ code: String?) {
        guard let completion else { return }
        self.completion = nil
        pasteField.text = nil
        for web in [webView].compactMap({ $0 }) + popups {
            web.stopLoading()
            web.navigationDelegate = nil
            web.uiDelegate = nil
        }
        popups.removeAll()
        webView = nil
        dismiss(animated: true)
        completion(code)
    }
}
