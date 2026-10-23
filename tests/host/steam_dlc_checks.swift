// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Steam DLC rules (app/Madeira/BCDSteamDLC/BCDSteamDLC.swift), compiled and run by tests/host/check-steam-dlc.py with
// SteamAppInfo.swift, SteamKeyValues.swift, SteamInstall.swift, AppManifestWriter.swift and the VDF parser of
// SteamLibraryFetcher.swift. Fixtures are synthetic: no Steam, no network.

import Foundation

var failures = 0
func check(_ what: String, _ ok: Bool, _ detail: Any = "") {
    print((ok ? "ok   " : "FAIL ") + what + (ok ? "" : "\n     \(detail)"))
    if !ok { failures += 1 }
}

let args = CommandLine.arguments
let fixtureDir = URL(fileURLWithPath: args[1])
func fixture(_ name: String) -> Data { try! Data(contentsOf: fixtureDir.appendingPathComponent(name)) }
func ids(_ depots: [SteamAppInfo.DepotInfo]) -> [UInt32] { depots.map(\.depotID) }

// ---- the app info: DLC list, depots, owner of the content -----------------------------------------
let base = SteamAppInfo.parse(appID: 1000, from: fixture("base.vdf"))!
check("the app lists its DLC (extended.listofdlc), without itself and without repeats", base.dlcList == [2001, 2002, 2003, 2004, 2005], base.dlcList)
check("the DLC the app knows are its list and the ones its depots name", base.dlcApps == [2001, 2002, 2003, 2004, 2005, 2007], base.dlcApps.sorted())
check("an app without a list has none", SteamAppInfo.parse(appID: 1000, from: fixture("plain.vdf"))!.dlcList.isEmpty)

// ---- depot selection ---------------------------------------------------------------------------------
let today = ids(base.installDepots())
check("today's install takes the common and English depots and no DLC depot", today == [1001, 1002], today)
check("with no DLC chosen the depot list is the one of today (same depots, same order, same manifests)",
      zip(base.installDepots(including: []), base.installDepots()).allSatisfy { $0.depotID == $1.depotID && $0.publicManifestID == $1.publicManifestID }
        && base.installDepots(including: []).count == base.installDepots().count)
check("a DLC the app does not have adds nothing", ids(base.installDepots(including: [9999])) == today)
check("DLC 2001 adds its two Windows depots (not the one another app owns, not the low-violence one)",
      ids(base.installDepots(including: [2001])) == [1001, 1002, 2011, 2012], ids(base.installDepots(including: [2001])))
check("two DLC add theirs, in depot order", ids(base.installDepots(including: [2001, 2002])) == [1001, 1002, 2011, 2012, 2021])
check("a DLC whose depots are for another platform adds nothing", ids(base.installDepots(including: [2003])) == today)
check("a DLC depot without a public manifest adds nothing", ids(base.installDepots(including: [2005])) == today)
check("DLC depots go by what the app lists, not by what the account owns (ownership is Steam's to decide)",
      ids(base.installDepots(including: [2004])) == [1001, 1002, 2041])
check("DLC depots of the other platform's language are left out", !ids(base.installDepots(including: [2001, 2002, 2004])).contains(2013))
let byDLC = base.dlcDepots()
check("dlcDepots groups the depots a Windows install could take by DLC app", byDLC.keys.sorted() == [2001, 2002, 2004, 2007] && byDLC[2001]?.count == 2, byDLC.keys.sorted())
check("with the old rules the base depot list is not changed by DLC: installDepots() still excludes every DLC depot",
      base.installDepots().allSatisfy { $0.dlcAppID == nil })

// The 32-bit fallback: an app that publishes only 32-bit depots; its DLC follows.
let old = SteamAppInfo.parse(appID: 3000, from: fixture("old32.vdf"))!
check("an app with only 32-bit depots falls back to them; today's list is unchanged", ids(old.installDepots()) == [3001], ids(old.installDepots()))
check("its DLC follows the architecture the game uses (the 32-bit DLC depot, not the 64-bit one)",
      ids(old.installDepots(including: [3101])) == [3001, 3111], ids(old.installDepots(including: [3101])))

// ---- what the account owns (license packages) ------------------------------------------------------
let packages = [fixture("pkg-a.bin"), fixture("pkg-b.bin")]
let owned = SteamDLC.ownedApps(inPackages: packages)
check("the apps of the license packages are what the account owns", owned == [1000, 2001, 2002, 2003, 2005, 2006, 2007], owned.sorted())
let names: [UInt32: String] = [2001: "Zeta Pack", 2002: "Alpha Pack", 2003: "Soundtrack", 2007: "Hidden Depots DLC"]
let report = SteamDLC.report(for: base, ownedApps: owned, names: names)
check("owned DLC with files are listed, by name", report.owned.map(\.appID) == [2002, 2007, 2001], report.owned.map { "\($0.appID) \($0.name)" })
let item2001 = report.owned.first { $0.appID == 2001 }!
check("a DLC lists its depots and sums their sizes (the modern figure, else the legacy maxsize)",
      item2001.depotIDs == [2011, 2012] && item2001.downloadBytes == 200 + 90 && item2001.sizeBytes == 400 + 150, "\(item2001)")
check("owned DLC with no Windows files is counted, not listed (platform, no manifest)", report.ownedWithoutContent == 2, report.ownedWithoutContent)
check("DLC the account does not own is a count", report.notOwned == 1, report.notOwned)
check("the totals are the owned DLC's", report.downloadBytes == report.owned.reduce(0) { $0 + $1.downloadBytes })
let unnamed = SteamDLC.report(for: base, ownedApps: [2001], names: [:])
check("a DLC without a name reads DLC <App ID>", unnamed.owned.first?.name == "DLC 2001")
check("nothing owned: an empty report", SteamDLC.report(for: base, ownedApps: [1000], names: names).isEmpty)
let plain = SteamAppInfo.parse(appID: 1000, from: fixture("plain.vdf"))!
check("a game without DLC has an empty report", SteamDLC.report(for: plain, ownedApps: owned, names: names) == SteamDLCReport(appID: 1000))

// ---- the job ----------------------------------------------------------------------------------------
func recordDepot(_ depot: UInt32, _ dlc: UInt32?, _ gid: UInt64 = 5) -> SteamDLCRecord.Depot {
    SteamDLCRecord.Depot(depotID: depot, manifest: gid, size: 100, dlcAppID: dlc)
}
check("nothing installed and nothing chosen: no DLC work (and no lookup needed)",
      SteamDLC.request(app: base, wanted: nil, installed: [:], ownedApps: owned) == .none)
check("the same when the account's apps could not be read", SteamDLC.request(app: base, wanted: nil, installed: [:], ownedApps: nil) == .none)
let first = SteamDLC.request(app: base, wanted: [2001, 2002], installed: [:], ownedApps: owned)
check("a first install with two chosen DLC takes both", first.selected == [2001, 2002] && first.removed.isEmpty && first.carried.isEmpty && first.owned == 5, "\(first)")
let subset = SteamDLC.request(app: base, wanted: [2002], installed: [:], ownedApps: owned)
check("a first install takes exactly the subset the player chose in the chooser (2001 is owned and listed, but not chosen)",
      subset.selected == [2002] && subset.extraDepots.isEmpty && subset.removed.isEmpty && subset.carried.isEmpty
        && ids(base.installDepots(including: subset.selected)) == [1001, 1002, 2021], "\(subset)")
check("the subset can be one DLC of many, or all of them",
      SteamDLC.request(app: base, wanted: [2001], installed: [:], ownedApps: owned).selected == [2001]
        && SteamDLC.request(app: base, wanted: [2001, 2002, 2007], installed: [:], ownedApps: owned).selected == [2001, 2002, 2007])
check("a DLC chosen that the account does not own, or that is not the game's, is not taken (the chooser only lists owned ones)",
      SteamDLC.request(app: base, wanted: [2002, 2004, 9999], installed: [:], ownedApps: owned).selected == [2002])
check("the game alone, chosen: nothing is taken, nothing removed, the owned DLC counted for the log",
      SteamDLC.request(app: base, wanted: [], installed: [:], ownedApps: owned).hasWork == false
        && SteamDLC.request(app: base, wanted: [], installed: [:], ownedApps: owned).owned == 5)
let update = SteamDLC.request(app: base, wanted: nil, installed: [2001: [recordDepot(2011, 2001), recordDepot(2012, 2001)]], ownedApps: owned)
check("an update keeps the installed DLC (never chosen: what is installed stays)", update.selected == [2001] && update.removed.isEmpty, "\(update)")
let drop = SteamDLC.request(app: base, wanted: [2002], installed: [2001: [recordDepot(2011, 2001, 7), recordDepot(2012, 2001, 8)]], ownedApps: owned)
check("a DLC the player took away is removed, one the player added is taken",
      drop.selected == [2002] && drop.removedApps == [2001] && drop.removed.map(\.depotID) == [2011, 2012] && drop.removed.map(\.manifest) == [7, 8], "\(drop)")
let revoked = SteamDLC.request(app: base, wanted: [2001, 2004], installed: [2004: [recordDepot(2041, 2004)]], ownedApps: owned)
check("an installed DLC the account no longer owns stays recorded and is not removed or taken",
      revoked.selected == [2001] && revoked.carried.map(\.depotID) == [2041] && revoked.removed.isEmpty, "\(revoked)")
let delisted = SteamDLC.request(app: base, wanted: nil, installed: [9001: [recordDepot(9011, 9001)]], ownedApps: owned)
check("an installed DLC the app no longer lists stays recorded", delisted.selected.isEmpty && delisted.carried.map(\.depotID) == [9011], "\(delisted)")
check("without the account's apps (unreadable) nothing is filtered by ownership: Steam's own refusal decides",
      SteamDLC.request(app: base, wanted: [2004], installed: [:], ownedApps: nil).selected == [2004])
check("the removal-only job is marked", SteamDLC.request(app: base, wanted: [], installed: [2001: [recordDepot(2011, 2001)]], ownedApps: owned, removalOnly: true).removalOnly)

// ---- DLC that declare their own depots (device round 1: X-Plane 12's maps) -----------------------------------------------
let xp = SteamAppInfo.parse(appID: 4000, from: fixture("xp.vdf"))!
let dlc4101 = SteamAppInfo.parse(appID: 4101, from: fixture("dlc4101.vdf"))!
let dlc4102 = SteamAppInfo.parse(appID: 4102, from: fixture("dlc4102.vdf"))!
let dlc4103 = SteamAppInfo.parse(appID: 4103, from: fixture("dlc4103.vdf"))!
let dlc4104 = SteamAppInfo.parse(appID: 4104, from: fixture("dlc4104.vdf"))!
check("a DLC app's product info reads as a DLC with its own depots", dlc4101.type == .dlc && dlc4101.name == "Scenery Europe" && ids(dlc4101.depots).sorted() == [4111, 4112, 4113, 4114])
check("its own depots are the ones with a public manifest, tagged as the DLC's (not the one another app owns)",
      ids(dlc4101.dlcOwnDepots()).sorted() == [4111, 4112, 4113] && dlc4101.dlcOwnDepots().allSatisfy { $0.dlcAppID == 4101 } && dlc4102.dlcOwnDepots().isEmpty)
let xpOwnedApps = SteamDLC.ownedApps(inPackages: [fixture("pkg-x.bin")])
check("the game lists no Windows depot for three of its four DLC; the fourth has one", SteamDLC.needingOwnInfo(app: xp, candidates: [4101, 4102, 4103, 4104]) == [4101, 4102, 4103]
      && SteamDLC.needingOwnInfo(app: xp, candidates: [4104]).isEmpty && SteamDLC.needingOwnInfo(app: xp, candidates: [4105, 9999]) == [4105],
      SteamDLC.needingOwnInfo(app: xp, candidates: [4101, 4102, 4103, 4104]))
let xpNames: [UInt32: String] = [4101: "Scenery Europe", 4102: "Scenery Asia", 4103: "Airports", 4104: "Aircraft Pack"]
let xpBefore = SteamDLC.report(for: xp, ownedApps: xpOwnedApps, names: xpNames)
check("without the DLC apps' own product info the game's DLC read as 'no files to download' (what the device showed)",
      xpBefore.owned.map(\.appID) == [4104] && xpBefore.ownedWithoutContent == 3 && xpBefore.notOwned == 1, "\(xpBefore)")
let ownDepots = [dlc4101, dlc4102, dlc4103].flatMap { $0.dlcOwnDepots() }
let xpMerged = xp.addingDLCDepots(ownDepots)
let xpAfter = SteamDLC.report(for: xpMerged, ownedApps: xpOwnedApps, names: xpNames)
check("with them the owned DLC list their Windows depots, sizes and all; the one with nothing stays 'no files'",
      xpAfter.owned.map(\.appID) == [4104, 4103, 4101] && xpAfter.ownedWithoutContent == 1 && xpAfter.notOwned == 1
        && xpAfter.owned.first(where: { $0.appID == 4101 })?.depotIDs == [4111] && xpAfter.owned.first(where: { $0.appID == 4101 })?.downloadBytes == 400
        && xpAfter.owned.first(where: { $0.appID == 4101 })?.sizeBytes == 800 && xpAfter.owned.first(where: { $0.appID == 4103 })?.depotIDs == [4011]
        && xpAfter.owned.first(where: { $0.appID == 4103 })?.downloadBytes == 150, "\(xpAfter)")
check("a depot the game lists with a manifest stays as the game lists it (the DLC's own entry does not replace it)",
      xpMerged.depots.first(where: { $0.depotID == 4012 })?.publicManifestID == 412 && xp.addingDLCDepots(dlc4104.dlcOwnDepots()).depots.first(where: { $0.depotID == 4012 })?.publicManifestID == 412)
check("one the game lists without a manifest takes the DLC's own entry (manifest, sizes, tagged)",
      xpMerged.depots.first(where: { $0.depotID == 4011 })?.publicManifestID == 4031 && xpMerged.depots.first(where: { $0.depotID == 4011 })?.dlcAppID == 4103
        && xpMerged.depots.filter { $0.depotID == 4011 }.count == 1)
check("no DLC chosen: the depot list is the game's alone, merged or not (today's list)",
      ids(xp.installDepots()) == [4001] && ids(xpMerged.installDepots()) == [4001] && ids(xpMerged.installDepots(including: [])) == [4001])
check("the DLC chosen bring their own depots, Windows only, into the game's install",
      ids(xpMerged.installDepots(including: [4101])) == [4001, 4111] && ids(xpMerged.installDepots(including: [4101, 4103, 4104])) == [4001, 4011, 4012, 4111]
        && ids(xpMerged.installDepots(including: [4102])) == [4001] && ids(xp.installDepots(including: [4101])) == [4001])
let ownRequest = SteamDLC.request(app: xp, wanted: [4101, 4103], installed: [:], ownedApps: xpOwnedApps, ownDepots: ownDepots)
check("the job takes them: selected, and the depots the downloader adds (Windows ones of the selected DLC only)",
      ownRequest.selected == [4101, 4103] && ids(ownRequest.extraDepots).sorted() == [4011, 4111] && ownRequest.owned == 4, "\(ownRequest)")
let ownSubset = SteamDLC.request(app: xp, wanted: [4103], installed: [:], ownedApps: xpOwnedApps, ownDepots: ownDepots)
check("a subset of DLC that declare their own depots brings only the chosen DLC's depots",
      ownSubset.selected == [4103] && ids(ownSubset.extraDepots) == [4011], "\(ownSubset)")
check("without the own depots the same choice takes nothing (the DLC cannot be installed)",
      SteamDLC.request(app: xp, wanted: [4101, 4103], installed: [:], ownedApps: xpOwnedApps).hasWork == false)
check("nothing chosen and nothing installed stays no work, with or without own depots",
      SteamDLC.request(app: xp, wanted: nil, installed: [:], ownedApps: xpOwnedApps, ownDepots: ownDepots) == .none)
let ownUpdate = SteamDLC.request(app: xp, wanted: nil, installed: [4101: [SteamDLCRecord.Depot(depotID: 4111, manifest: 4111, size: 800, dlcAppID: 4101)]],
                                 ownedApps: xpOwnedApps, ownDepots: ownDepots)
check("an update keeps an installed own-depot DLC and brings its depot again", ownUpdate.selected == [4101] && ids(ownUpdate.extraDepots) == [4111] && ownUpdate.removed.isEmpty, "\(ownUpdate)")
let ownInstalled = SteamDLCRecord.tag([AppManifestWriter.InstalledDepot(depotID: 4001, manifestGID: 401, size: 9000),
                                       AppManifestWriter.InstalledDepot(depotID: 4111, manifestGID: 4111, size: 800)],
                                      dlcByDepot: SteamDLC.dlcByDepot(xpMerged.installDepots(including: [4101])))
try! AppManifestWriter.writeManifest(appID: 4000, name: "Flight Game", installDir: "Flight Game", buildID: 1, steamID: 1, sizeOnDisk: 9800,
                                     steamAppsPath: FileManager.default.temporaryDirectory.path, installedDepots: ownInstalled)
let ownRecordURL = FileManager.default.temporaryDirectory.appendingPathComponent("appmanifest_4000.acf")
let ownRecord = (try? String(contentsOf: ownRecordURL, encoding: .utf8)) ?? ""
try? FileManager.default.removeItem(at: ownRecordURL)
check("the DLC's own depot is recorded in the game's appmanifest with its dlcappid, like a listed one",
      ownRecord.contains("\t\t\"4111\"\n\t\t{\n\t\t\t\"manifest\"\t\t\"4111\"\n\t\t\t\"size\"\t\t\"800\"\n\t\t\t\"dlcappid\"\t\t\"4101\"\n\t\t}")
        && !ownRecord.contains("\"4001\"\n\t\t{\n\t\t\t\"manifest\"\t\t\"401\"\n\t\t\t\"size\"\t\t\"9000\"\n\t\t\t\"dlcappid\""), ownRecord)
check("a request knows its extra depots when compared", ownRequest != SteamDLC.request(app: xp, wanted: [4101, 4103], installed: [:], ownedApps: xpOwnedApps, ownDepots: Array(ownDepots.prefix(1))))

// ---- the install record -------------------------------------------------------------------------------
let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("steam-dlc-check-\(getpid())", isDirectory: true)
try? FileManager.default.removeItem(at: tmp)
try! FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
let apps = tmp.appendingPathComponent("steamapps", isDirectory: true)
try! FileManager.default.createDirectory(at: apps, withIntermediateDirectories: true)

func recordText(_ appID: Int) -> String { (try? String(contentsOf: apps.appendingPathComponent("appmanifest_\(appID).acf"), encoding: .utf8)) ?? "" }
func normalized(_ text: String) -> String {
    text.components(separatedBy: "\n").map { $0.contains("\"LastUpdated\"") ? "\"LastUpdated\"" : $0 }.joined(separator: "\n")
}
let installedBase = [AppManifestWriter.InstalledDepot(depotID: 1001, manifestGID: 111, size: 1000),
                     AppManifestWriter.InstalledDepot(depotID: 1002, manifestGID: 112, size: 100)]
try! AppManifestWriter.writeManifest(appID: 1000, name: "Test Game", installDir: "Test Game", buildID: 777, steamID: 76561190000000000,
                                     sizeOnDisk: 1100, steamAppsPath: apps.path, installedDepots: installedBase)
let todaysRecord = normalized(recordText(1000))
try! AppManifestWriter.writeManifest(appID: 1000, name: "Test Game", installDir: "Test Game", buildID: 777, steamID: 76561190000000000,
                                     sizeOnDisk: 1100 + 0, steamAppsPath: apps.path,
                                     installedDepots: SteamDLCRecord.installedDepots(SteamDLCRecord.tag(installedBase, dlcByDepot: [:]), carried: []))
check("with no DLC the install record is the one of today, byte for byte", normalized(recordText(1000)) == todaysRecord && !todaysRecord.contains("dlcappid"))

let withDLC = SteamDLCRecord.tag(installedBase + [AppManifestWriter.InstalledDepot(depotID: 2011, manifestGID: 211, size: 400),
                                                  AppManifestWriter.InstalledDepot(depotID: 2012, manifestGID: 212, size: 150),
                                                  AppManifestWriter.InstalledDepot(depotID: 2021, manifestGID: 221, size: 60)],
                                 dlcByDepot: SteamDLC.dlcByDepot(base.installDepots(including: [2001, 2002])))
try! AppManifestWriter.writeManifest(appID: 1000, name: "Test Game", installDir: "Test Game", buildID: 777, steamID: 76561190000000000,
                                     sizeOnDisk: 1710, steamAppsPath: apps.path, installedDepots: withDLC,
                                     sharedDepots: [(depotID: 228988, ownerAppID: 228980)], customExecutables: ["bin\\game.exe"])
let text = recordText(1000)
check("the DLC depots are in InstalledDepots with manifest, size and dlcappid, as Valve records them",
      text.contains("\t\t\"2011\"\n\t\t{\n\t\t\t\"manifest\"\t\t\"211\"\n\t\t\t\"size\"\t\t\"400\"\n\t\t\t\"dlcappid\"\t\t\"2001\"\n\t\t}")
        && text.contains("\"dlcappid\"\t\t\"2002\"") && !text.contains("\"1001\"\n\t\t{\n\t\t\t\"manifest\"\t\t\"111\"\n\t\t\t\"size\"\t\t\"1000\"\n\t\t\t\"dlcappid\""), text)
let state = SteamDLCRecord.read(appID: 1000, steamApps: apps)!
check("the record reads back: depots, manifests, sizes and the DLC of each",
      state.depots.map(\.depotID) == [1001, 1002, 2011, 2012, 2021] && state.depots.map(\.dlcAppID) == [nil, nil, 2001, 2001, 2002]
        && state.depots.first(where: { $0.depotID == 2012 })?.manifest == 212 && state.sizeOnDisk == 1710, "\(state)")
let installed = SteamDLCRecord.installedDLC(appID: 1000, steamApps: apps)
check("the installed DLC are the ones with a dlcappid, by DLC app", installed.keys.sorted() == [2001, 2002] && installed[2001]?.count == 2)
check("a game without a record has none installed", SteamDLCRecord.installedDLC(appID: 5555, steamApps: apps).isEmpty)
let carriedRecord = SteamDLCRecord.installedDepots(installedBase, carried: [recordDepot(2041, 2004, 41), recordDepot(1001, nil)])
check("a carried entry stays in the record, sorted in; a depot the run installed is not doubled",
      carriedRecord.map(\.depotID) == [1001, 1002, 2041] && carriedRecord.last?.dlcAppID == 2004 && carriedRecord.first?.manifestGID == 111)

// Taking a DLC away rewrites the record without its depots and keeps the rest.
try! SteamDLCRecord.rewrite(appID: 1000, steamApps: apps, dropping: [2011, 2012])
let after = SteamDLCRecord.read(appID: 1000, steamApps: apps)!
check("removing DLC 2001 drops its depots and the bytes they held from the record",
      after.depots.map(\.depotID) == [1001, 1002, 2021] && after.sizeOnDisk == 1710 - 550 && after.buildID == 777
        && after.name == "Test Game" && after.installDir == "Test Game" && after.lastOwner == 76561190000000000, "\(after)")
check("... and keeps the shared depots and the custom executables", after.shared == [SteamDLCRecord.SharedDepot(depotID: 228988, ownerAppID: 228980)]
        && after.customExecutables == ["bin\\game.exe"], "\(after)")
check("... the DLC left is still found installed", SteamDLCRecord.installedDLC(appID: 1000, steamApps: apps).keys.sorted() == [2002])
do {
    try SteamDLCRecord.rewrite(appID: 4242, steamApps: apps, dropping: [1])
    check("rewriting a record that is not there fails", false)
} catch { check("rewriting a record that is not there fails", true) }
let hostile = apps.appendingPathComponent("appmanifest_777.acf")
try! Data("\"AppState\"\n{\n\"appid\" \"778\"\n\"InstalledDepots\" { \"1\" { \"dlcappid\" \"9\" } }\n}\n".utf8).write(to: hostile)
check("a record that names another app is not read", SteamDLCRecord.read(appID: 777, steamApps: apps) == nil)

// ---- taking the files away ----------------------------------------------------------------------------
check("only the files no depot that stays holds are taken (compared like Windows: case and slashes)",
      SteamDLCRemoval.exclusivePaths(removed: ["Data\\DLC1\\a.pak", "data/dlc1/b.pak", "bin/game.exe", "Data/DLC1/a.pak"],
                                     kept: ["BIN\\GAME.EXE", "data/other.pak"]) == ["Data\\DLC1\\a.pak", "data/dlc1/b.pak"])
let install = tmp.appendingPathComponent("common/Test Game", isDirectory: true)
func put(_ relative: String, _ body: String = "x") {
    let url = install.appendingPathComponent(relative)
    try! FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try! Data(body.utf8).write(to: url)
}
put("Game.exe"); put("Data/other.pak"); put("Data/DLC1/a.pak"); put("Data/DLC1/sub/b.pak"); put("Data/keep/shared.bin")
let outside = tmp.appendingPathComponent("outside.txt")
try! Data("keep".utf8).write(to: outside)
try! FileManager.default.createSymbolicLink(at: install.appendingPathComponent("link.pak"), withDestinationURL: outside)
let deleted = SteamDLCRemoval.delete(["Data/dlc1/A.PAK", "Data\\DLC1\\sub\\b.pak", "link.pak", "../outside.txt", "Data/missing.pak", "Data/DLC1", "C:/x"], in: install)
let fm = FileManager.default
check("the DLC's files are found without case and deleted; folders the removal emptied go too", deleted == 2
        && !fm.fileExists(atPath: install.appendingPathComponent("Data/DLC1").path), deleted)
check("what the game holds stays", fm.fileExists(atPath: install.appendingPathComponent("Game.exe").path)
        && fm.fileExists(atPath: install.appendingPathComponent("Data/other.pak").path)
        && fm.fileExists(atPath: install.appendingPathComponent("Data/keep/shared.bin").path) && fm.fileExists(atPath: install.path))
check("a link is never followed or deleted, a path out of the folder or a folder is ignored",
      fm.fileExists(atPath: outside.path) && (try? fm.destinationOfSymbolicLink(atPath: install.appendingPathComponent("link.pak").path)) != nil)

// ---- the player's choice ------------------------------------------------------------------------------
let choiceFile = tmp.appendingPathComponent("support/steam-dlc.json")
let choices = SteamDLCChoices(file: choiceFile)
check("a game never asked has no choice", choices.wanted(1000) == nil)
choices.set(1000, wanted: [2002, 2001]); choices.set(620, wanted: [])
let reread = SteamDLCChoices(file: choiceFile)
check("the choice is kept in a file, the game alone included", reread.wanted(1000) == [2001, 2002] && reread.wanted(620) == Set<UInt32>() && reread.wanted(1) == nil)
reread.forget(1000)
check("a forgotten choice is gone", SteamDLCChoices(file: choiceFile).wanted(1000) == nil && SteamDLCChoices(file: choiceFile).wanted(620) == Set<UInt32>())
try! Data("not json".utf8).write(to: choiceFile)
check("a file that is not the choice file is ignored", SteamDLCChoices(file: choiceFile).wanted(620) == nil)

// ---- the log ------------------------------------------------------------------------------------------
let req = SteamDLC.request(app: base, wanted: [2001], installed: [2002: [recordDepot(2021, 2002)]], ownedApps: owned)
let line = SteamDLC.summaryLine(app: 1000, request: req, depots: base.installDepots(including: req.selected))
check("the summary line: app, owned, selected, depots, bytes", line == "[steam-dlc] app=1000 owned=5 selected=1 depots=2011,2012 bytes=290 removing=1", line)
check("the summary line of a game alone has no depots", SteamDLC.summaryLine(app: 1000, request: SteamDLCRequest(owned: 5), depots: base.installDepots())
        == "[steam-dlc] app=1000 owned=5 selected=0 depots=- bytes=0")
let results = SteamDLC.resultLines(app: 1000, request: req, depots: base.installDepots(including: req.selected), planned: [1001, 1002, 2011, 2012],
                                   refused: [], notRemoved: [])
check("per-DLC results: installed, removed", results == ["[steam-dlc] result app=1000 dlc=2001 depots=2/2 state=installed",
                                                          "[steam-dlc] result app=1000 dlc=2002 depots=1 state=removed"], results)
let partial = SteamDLC.resultLines(app: 1000, request: req, depots: base.installDepots(including: req.selected), planned: [1001, 2011], refused: [], notRemoved: [2002])
check("... partial and a removal that failed", partial == ["[steam-dlc] result app=1000 dlc=2001 depots=1/2 state=partial",
                                                           "[steam-dlc] result app=1000 dlc=2002 depots=1 state=remove-failed"], partial)
let refused = SteamDLC.resultLines(app: 1000, request: SteamDLCRequest(selected: [2001]), depots: base.installDepots(including: [2001]), planned: [1001, 1002], refused: [2001], notRemoved: [])
check("... and a DLC Steam gave no key for", refused == ["[steam-dlc] result app=1000 dlc=2001 depots=0/2 state=refused"], refused)
for text in [line] + results + partial + refused {
    check("no account data in the log line: \(text.prefix(48))…", !text.lowercased().contains("steamid") && !text.contains("7656") && !text.contains("@") && !text.lowercased().contains("token"))
}

try? FileManager.default.removeItem(at: tmp)
print(failures == 0 ? "steam dlc rules: all passed" : "steam dlc rules: \(failures) failed")
exit(failures == 0 ? 0 : 1)
