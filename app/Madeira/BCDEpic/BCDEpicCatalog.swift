// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import CryptoKit
import Foundation

// The account's Epic games and their pictures (docs/research/open-fork-2026-10-18/epic.md §2.2): the
// owned items from Epic's library service (paged by a cursor), then each item's catalog entry from
// the catalog service (a bulk call per namespace, at most four at a time) for its title, pictures,
// kind and the few attributes later milestones need. The list is cached on disk per account (a
// SHA-256 of the account ID, never the ID, and no token). Written from the protocol facts in the
// design's tables; no code of any other Epic client. Foundation and CryptoKit only.

/// One owned Epic item and what its catalog entry said.
struct BCDEpicItem: Codable, Hashable, Identifiable {
    enum Kind: String, Codable {
        /// A game BCD lists.
        case game
        /// Downloadable content of another game, a mod, or an extra (a soundtrack, an artbook): not listed.
        case dlc, mod, extra
        /// The catalog names platforms and Windows is not one of them.
        case notWindows
        /// The catalog entry is not known yet (the call failed or has not run).
        case unknown
    }

    /// Epic's app name for the game: its ID everywhere in BCD ("epic.<app name>").
    var id: String
    var namespace: String
    var catalogItemId: String
    var kind: Kind = .unknown
    var title: String?
    /// The portrait and the wide picture (https addresses from Epic's catalog).
    var tall: String?
    var wide: String?
    // Kept for the next milestones (install and launch): the catalog's own attributes.
    var folderName: String?
    var canRunOffline: Bool?
    var ownershipToken: Bool?
    var thirdParty: String?
    var accessControl: Bool?
    /// The catalog's extra command line for the game ("AdditionalCommandLine"): plain text, passed on at Play.
    var extraArguments: String?
    /// When the catalog entry was read, and which attributes this app reads from it (BCDEpicCatalog.revision):
    /// an entry read before an attribute was kept is read again.
    var fetched: Date?
    var revision: Int?
}

/// A game's pictures, each a list of addresses tried in order (BCDGame's shapes).
struct BCDEpicArt: Equatable {
    var cover: [URL] = []
    var header: [URL] = []
    var hero: [URL] = []
}

/// One record of Epic's library service.
struct BCDEpicRecord: Equatable {
    var appName: String
    var namespace: String
    var catalogItemId: String
}

/// What one refresh read and decided, for the log (counts only).
struct BCDEpicLibraryStats: Equatable {
    var records = 0, pages = 0
    var droppedUnreal = 0, droppedPrivate = 0, droppedInvalid = 0, duplicates = 0
    var namespaces = 0, catalogRequests = 0, catalogFailed = 0, reused = 0
    var games = 0, dlc = 0, mods = 0, extras = 0, notWindows = 0, unknown = 0
    var thirdParty = 0
    var statuses: [Int: Int] = [:]
    var milliseconds = 0

    var summary: String {
        "records=\(records) pages=\(pages) games=\(games) dlc=\(dlc) mods=\(mods) extras=\(extras) other-platform=\(notWindows) "
            + "unknown=\(unknown) third-party=\(thirdParty) dropped-ue=\(droppedUnreal) dropped-private=\(droppedPrivate) "
            + "dropped-invalid=\(droppedInvalid) duplicates=\(duplicates) namespaces=\(namespaces) catalog-requests=\(catalogRequests) "
            + "catalog-failed=\(catalogFailed) reused=\(reused) statuses=\(BCDEpicLog.statuses(statuses)) ms=\(milliseconds)"
    }
}

enum BCDEpicCatalog {
    /// The attributes read from a catalog entry: 2 added the extra command line (milestone 1).
    static let revision = 2
    static let libraryHost = "library-service.live.use1a.on.epicgames.com"
    static let catalogHost = "catalog-public-service-prod06.ol.epicgames.com"

    static func libraryURL(cursor: String?) -> URL {
        var text = "https://\(libraryHost)/library/api/public/items?includeMetadata=true"
        if let cursor { text += "&cursor=" + BCDEpicAuth.encode(cursor) }
        return URL(string: text)!
    }

    static func bulkURL(namespace: String, ids: [String]) -> URL {
        let query = ids.map { "id=" + BCDEpicAuth.encode($0) }.joined(separator: "&")
        return URL(string: "https://\(catalogHost)/catalog/api/shared/namespace/\(BCDEpicAuth.encode(namespace))/bulk/items?" + query
            + "&includeDLCDetails=true&includeMainGameDetails=true&country=US&locale=en-US")!
    }

    private static func isName(_ text: String, max: Int) -> Bool {
        (1...max).contains(text.utf8.count) && text.utf8.allSatisfy {
            ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x41 && $0 <= 0x5A) || ($0 >= 0x61 && $0 <= 0x7A) || $0 == 0x5F || $0 == 0x2D || $0 == 0x2E
        }
    }

    static func validAppName(_ text: String) -> Bool { isName(text, max: 128) }
    static func validNamespace(_ text: String) -> Bool { isName(text, max: 64) }
    static func validCatalogID(_ text: String) -> Bool { isName(text, max: 64) }

    // MARK: The owned list

    struct LibraryPage {
        var records: [BCDEpicRecord] = []
        var next: String?
        /// What the page held before any filter.
        var raw = 0
        var droppedUnreal = 0, droppedPrivate = 0, droppedInvalid = 0
    }

    /// One page of the library service's reply: its records (Unreal Engine's own assets, private
    /// sandboxes and records without a usable name dropped) and the cursor of the next page.
    static func parseLibraryPage(_ object: [String: Any]) throws -> LibraryPage {
        var list = object["records"] as? [[String: Any]]
        if list == nil {
            list = object.values.compactMap { $0 as? [[String: Any]] }.first { ($0.first?["catalogItemId"] ?? $0.first?["appName"]) != nil }
        }
        guard let items = list else { throw BCDEpicError.badReply("library") }
        var page = LibraryPage()
        page.raw = items.count
        for item in items {
            let app = (item["appName"] as? String) ?? "", namespace = (item["namespace"] as? String) ?? ""
            let catalogID = (item["catalogItemId"] as? String) ?? ""
            guard validAppName(app), validNamespace(namespace), validCatalogID(catalogID) else { page.droppedInvalid += 1; continue }
            if namespace == "ue" { page.droppedUnreal += 1; continue }
            if ((item["sandboxType"] as? String) ?? "").uppercased() == "PRIVATE" { page.droppedPrivate += 1; continue }
            page.records.append(BCDEpicRecord(appName: app, namespace: namespace, catalogItemId: catalogID))
        }
        if let meta = object["responseMetadata"] as? [String: Any], let next = meta["nextCursor"] as? String,
           !next.isEmpty, next.utf8.count <= 512, next.utf8.allSatisfy({ $0 > 0x20 && $0 < 0x7F }) {
            page.next = next
        }
        return page
    }

    // MARK: Catalog entries

    struct Details: Equatable {
        var kind: BCDEpicItem.Kind
        var title: String?
        var tall: String?
        var wide: String?
        var folderName: String?
        var canRunOffline: Bool?
        var ownershipToken: Bool?
        var thirdParty: String?
        var accessControl: Bool?
        var extraArguments: String?
    }

    /// The bulk reply: a dictionary from catalog item ID to its entry (an array of entries is read too).
    static func parseBulk(_ object: [String: Any]) -> [String: Details] {
        var out: [String: Details] = [:]
        for (key, value) in object {
            if let entry = value as? [String: Any] {
                let id = (entry["id"] as? String) ?? key
                if validCatalogID(id), let details = details(entry) { out[id] = details }
            } else if let list = value as? [[String: Any]] {
                for entry in list {
                    if let id = entry["id"] as? String, validCatalogID(id), let details = details(entry) { out[id] = details }
                }
            }
        }
        return out
    }

    private static func details(_ entry: [String: Any]) -> Details? {
        guard let rawTitle = entry["title"] as? String else { return nil }
        let title = rawTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return nil }
        let attributes = entry["customAttributes"] as? [String: Any] ?? [:]
        func attribute(_ name: String) -> String? {
            if let item = attributes[name] as? [String: Any], let value = item["value"] as? String { return value }
            return attributes[name] as? String
        }
        func flag(_ name: String) -> Bool? {
            guard let value = attribute(name)?.lowercased() else { return nil }
            return value == "true" || value == "1" ? true : value == "false" || value == "0" ? false : nil
        }
        let images = pictures(entry["keyImages"] as? [[String: Any]] ?? [])
        var third: String?
        if let provider = attribute("ThirdPartyManagedProvider")?.trimmingCharacters(in: .whitespaces), !provider.isEmpty {
            third = String(provider.prefix(40))
        } else if let app = attribute("ThirdPartyManagedApp")?.trimmingCharacters(in: .whitespaces), !app.isEmpty {
            third = "yes"
        }
        let folder = attribute("FolderName")?.trimmingCharacters(in: .whitespaces)
        let extra = (attribute("AdditionalCommandLine") ?? attribute("AdditionalCommandline"))?.trimmingCharacters(in: .whitespacesAndNewlines)
        return Details(kind: kind(entry), title: String(title.prefix(200)), tall: images.tall, wide: images.wide,
                       folderName: folder.flatMap { $0.isEmpty || $0.utf8.count > 200 ? nil : $0 },
                       canRunOffline: flag("CanRunOffline"), ownershipToken: flag("OwnershipToken"),
                       thirdParty: third, accessControl: flag("UseAccessControl"),
                       extraArguments: extra.flatMap { $0.isEmpty || $0.utf8.count > 1500 ? nil : $0 })
    }

    /// DLC (it names a main game), a mod, an extra, a title with no Windows build, or a game. An entry
    /// that names no platforms at all is left listed.
    static func kind(_ entry: [String: Any]) -> BCDEpicItem.Kind {
        if let main = entry["mainGameItem"], !(main is NSNull) { return .dlc }
        let paths = (entry["categories"] as? [[String: Any]] ?? []).compactMap { ($0["path"] as? String)?.lowercased() }
        func has(_ word: String) -> Bool { paths.contains { $0 == word || $0.hasPrefix(word + "/") } }
        if has("mods") { return .mod }
        if (has("addons") || has("digitalextras")) && !has("games") { return .extra }
        let releases = entry["releaseInfo"] as? [[String: Any]] ?? []
        var platforms: [String] = []
        for release in releases {
            if let list = release["platform"] as? [String] { platforms += list }
            else if let one = release["platform"] as? String { platforms.append(one) }
        }
        if !platforms.isEmpty, !platforms.contains(where: { $0.lowercased().hasPrefix("windows") }) { return .notWindows }
        return .game
    }

    /// The portrait and the wide picture: the box art by its type name, else the offer images, else
    /// whichever picture is taller or wider than it is high.
    static func pictures(_ images: [[String: Any]]) -> (tall: String?, wide: String?) {
        func url(_ image: [String: Any]) -> String? { imageURL(image["url"] as? String) }
        func first(type: String) -> String? { images.first { ($0["type"] as? String) == type }.flatMap(url) }
        func number(_ image: [String: Any], _ key: String) -> Double { (image[key] as? NSNumber)?.doubleValue ?? 0 }
        let tall = first(type: "DieselGameBoxTall") ?? first(type: "OfferImageTall")
            ?? images.first { number($0, "height") > number($0, "width") && number($0, "width") > 0 }.flatMap(url)
        let wide = first(type: "DieselGameBox") ?? first(type: "OfferImageWide")
            ?? images.first { number($0, "width") > number($0, "height") && number($0, "height") > 0 }.flatMap(url)
        return (tall, wide)
    }

    /// A picture's address kept only as https (Epic's catalog may say http) without credentials.
    static func imageURL(_ text: String?) -> String? {
        guard var text = text?.trimmingCharacters(in: .whitespacesAndNewlines), text.utf8.count <= 600 else { return nil }
        if text.hasPrefix("http://") { text = "https://" + text.dropFirst(7) }
        guard let url = URL(string: text), url.scheme == "https", url.user == nil, let host = url.host, !host.isEmpty else { return nil }
        return text
    }

    static func art(_ item: BCDEpicItem) -> BCDEpicArt {
        func url(_ text: String?) -> URL? { text.flatMap(URL.init(string:)) }
        let tall = url(item.tall), wide = url(item.wide)
        return BCDEpicArt(cover: [tall, wide].compactMap { $0 }, header: [wide, tall].compactMap { $0 }, hero: [wide].compactMap { $0 })
    }

    // MARK: The cache

    /// The cached list belongs to one account: a SHA-256 of its ID, so the file holds no ID.
    static func accountKey(_ accountID: String) -> String {
        SHA256.hash(data: Data(accountID.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    struct Cache: Codable {
        var version = 1
        var account: String
        var updated: Date
        var items: [BCDEpicItem]
    }
}

/// A refresh of the whole list: the pages of owned records, then the catalog entries they lack.
enum BCDEpicLibrary {
    /// A catalog entry is read again after this long (they rarely change).
    static let catalogLifetime: TimeInterval = 14 * 86400

    private struct Batch {
        var namespace: String
        var ids: [String]
    }

    private struct BatchResult {
        var batch: Batch
        var details: [String: BCDEpicCatalog.Details]?
        var status: Int
        var error: BCDEpicError?
        var shape: String?
    }

    /// Reads the library and the catalog entries `known` does not hold (or holds stale or unknown) and
    /// returns every owned item, with the counts of what happened. Pages and catalog calls that fail
    /// leave their items unknown (kept from `known` when it had them); a refused sign-in is thrown.
    static func refresh(api: BCDEpicAPI, known: [BCDEpicItem], now: Date, maxPages: Int = 200, concurrency: Int = 4,
                        batchSize: Int = 25) async throws -> (items: [BCDEpicItem], stats: BCDEpicLibraryStats) {
        let started = BCDEpicLog.now()
        var stats = BCDEpicLibraryStats()
        var records: [BCDEpicRecord] = []
        var cursor: String?
        var seen = Set<String>()
        repeat {
            let (status, data) = try await api.get(BCDEpicCatalog.libraryURL(cursor: cursor))
            stats.pages += 1
            stats.statuses[status, default: 0] += 1
            guard let object = BCDEpicAuth.jsonObject(data) else {
                BCDEpicLog.write("[epic-library] reply is not a JSON object: bytes=\(data.count)")
                throw BCDEpicError.badReply("library")
            }
            let page: BCDEpicCatalog.LibraryPage
            do { page = try BCDEpicCatalog.parseLibraryPage(object) } catch {
                BCDEpicLog.write("[epic-library] reply is not a library page: \(BCDEpicLog.shape(object))")
                throw error
            }
            stats.records += page.raw
            stats.droppedUnreal += page.droppedUnreal
            stats.droppedPrivate += page.droppedPrivate
            stats.droppedInvalid += page.droppedInvalid
            records += page.records
            cursor = page.next
            if let next = cursor, !seen.insert(next).inserted { break }
        } while cursor != nil && stats.pages < maxPages

        var ordered: [BCDEpicRecord] = []
        var names = Set<String>()
        for record in records {
            if names.insert(record.appName).inserted { ordered.append(record) } else { stats.duplicates += 1 }
        }
        let old = Dictionary(known.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        func fresh(_ record: BCDEpicRecord) -> BCDEpicItem? {
            guard let item = old[record.appName], item.namespace == record.namespace, item.catalogItemId == record.catalogItemId,
                  item.kind != .unknown, item.revision == BCDEpicCatalog.revision,
                  let when = item.fetched, now.timeIntervalSince(when) < BCDEpicLibrary.catalogLifetime else { return nil }
            return item
        }
        var wanted: [String: [String]] = [:]
        var namespaceOrder: [String] = []
        for record in ordered where fresh(record) == nil {
            if wanted[record.namespace] == nil { namespaceOrder.append(record.namespace) }
            if !(wanted[record.namespace]?.contains(record.catalogItemId) ?? false) { wanted[record.namespace, default: []].append(record.catalogItemId) }
        }
        var batches: [Batch] = []
        for namespace in namespaceOrder {
            let ids = wanted[namespace] ?? []
            var at = 0
            while at < ids.count { batches.append(Batch(namespace: namespace, ids: Array(ids[at..<min(ids.count, at + batchSize)]))); at += batchSize }
        }
        stats.namespaces = namespaceOrder.count
        stats.catalogRequests = batches.count

        var results: [BatchResult] = []
        try await withThrowingTaskGroup(of: BatchResult.self) { group in
            var next = 0
            func add() {
                guard next < batches.count else { return }
                let batch = batches[next]
                next += 1
                group.addTask { await load(batch, api: api) }
            }
            for _ in 0..<max(1, concurrency) { add() }
            while let result = try await group.next() {
                results.append(result)
                add()
            }
        }
        var byID: [String: BCDEpicCatalog.Details] = [:]
        var shown = false
        for result in results {
            // A 200 that gave no entry for any ID asked: say what the reply looked like (once).
            if !shown, result.error == nil, (result.details ?? [:]).isEmpty, let shape = result.shape {
                shown = true
                BCDEpicLog.write("[epic-library] catalog reply gave no entry: \(shape)")
            }
            if result.status != 0 { stats.statuses[result.status, default: 0] += 1 }
            if let error = result.error {
                if error == .signInRefused || error == .signedOut { throw error }
                stats.catalogFailed += 1
            }
            for (id, details) in result.details ?? [:] { byID[result.batch.namespace + ":" + id] = details }
        }

        var items: [BCDEpicItem] = []
        for record in ordered {
            var item = BCDEpicItem(id: record.appName, namespace: record.namespace, catalogItemId: record.catalogItemId)
            if let kept = fresh(record) {
                item = kept
                stats.reused += 1
            } else if let details = byID[record.namespace + ":" + record.catalogItemId] {
                item.kind = details.kind
                item.title = details.title
                item.tall = details.tall
                item.wide = details.wide
                item.folderName = details.folderName
                item.canRunOffline = details.canRunOffline
                item.ownershipToken = details.ownershipToken
                item.thirdParty = details.thirdParty
                item.accessControl = details.accessControl
                item.extraArguments = details.extraArguments
                item.fetched = now
                item.revision = BCDEpicCatalog.revision
            } else if let stale = old[record.appName], stale.namespace == record.namespace, stale.catalogItemId == record.catalogItemId {
                item = stale
            }
            switch item.kind {
            case .game: stats.games += 1; if item.thirdParty != nil { stats.thirdParty += 1 }
            case .dlc: stats.dlc += 1
            case .mod: stats.mods += 1
            case .extra: stats.extras += 1
            case .notWindows: stats.notWindows += 1
            case .unknown: stats.unknown += 1
            }
            items.append(item)
        }
        stats.milliseconds = BCDEpicLog.millis(since: started)
        return (items, stats)
    }

    private static func load(_ batch: Batch, api: BCDEpicAPI) async -> BatchResult {
        do {
            let (status, data) = try await api.get(BCDEpicCatalog.bulkURL(namespace: batch.namespace, ids: batch.ids))
            guard let object = BCDEpicAuth.jsonObject(data) else { return BatchResult(batch: batch, details: nil, status: status, error: .badReply("catalog")) }
            let details = BCDEpicCatalog.parseBulk(object)
            return BatchResult(batch: batch, details: details, status: status, error: nil, shape: details.isEmpty ? BCDEpicLog.shape(object) : nil)
        } catch let error as BCDEpicError {
            var status = 0
            if case .rejected(let rejected, _) = error { status = rejected } else if case .http(let http) = error { status = http }
            return BatchResult(batch: batch, details: nil, status: status, error: error)
        } catch {
            return BatchResult(batch: batch, details: nil, status: 0, error: .badReply("net"))
        }
    }
}
