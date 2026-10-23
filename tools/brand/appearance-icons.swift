// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
import Foundation
import CoreGraphics
import CoreText
import ImageIO
import UniformTypeIdentifiers

// appearance-icons <AppIcon-1024.png> <outdir> [reference-icon.png]
// Makes the iOS 18+ dark and tinted app icons from the light one:
//   AppIcon-Dark-1024.png    the drawing on a transparent background (iOS draws its own dark background under it);
//   AppIcon-Tinted-1024.png  an opaque grayscale of the drawing on black (iOS tints it);
//   readme-icon-256.png      the dark icon as iOS shows it (its dark background, the rounded shape), for the README;
//   appearance-sheet.png     a preview: the light icon, an optional reference icon, the dark and the tinted one.
// The drawing is lifted off the artwork's flat background B by colour-to-alpha: the smallest alpha that explains
// each pixel as the drawing over B, so the glow keeps its look over B and turns into see-through light over
// anything else. Dark parts inside the drawing (the d-pad cross) become see-through too and show iOS's dark
// background, which reads the same.
let a = CommandLine.arguments
let srcURL = URL(fileURLWithPath: a[1]), out = URL(fileURLWithPath: a[2])
let cs = CGColorSpace(name: CGColorSpace.sRGB)!
func load(_ url: URL) -> CGImage { CGImageSourceCreateImageAtIndex(CGImageSourceCreateWithURL(url as CFURL, nil)!, 0, nil)! }
let image = load(srcURL)
let w = image.width, h = image.height
var px = [UInt8](repeating: 0, count: w * h * 4)
CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: cs,
          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
// The flat background: the median of the four corners.
var cr: [[Int]] = [[], [], []]
for (ox, oy) in [(0, 0), (w - 16, 0), (0, h - 16), (w - 16, h - 16)] {
    for y in oy..<oy + 16 { for x in ox..<ox + 16 { let i = (y * w + x) * 4; for c in 0..<3 { cr[c].append(Int(px[i + c])) } } }
}
let B = cr.map { Double($0.sorted()[$0.count / 2]) }
print("background", B)
// Colour-to-alpha: a pixel brighter than B is the drawing at alpha (C-B)/(255-B) (the largest channel decides);
// anything at or below B, including the artwork's slightly darker, textured ground, is background. A floor of 3%
// drops the faint noise of that ground, so no square shows where the artwork was faded into B.
let floor = 0.03
var dark = [UInt8](repeating: 0, count: w * h * 4)
var gray = [UInt8](repeating: 0, count: w * h * 4)
for p in 0..<(w * h) {
    var raw = 0.0
    for c in 0..<3 { raw = max(raw, (Double(px[p * 4 + c]) - B[c]) / (255 - B[c])) }
    let al = min(max((raw - floor) / (1 - floor), 0), 1)
    // Premultiplied drawing colour: what the pixel adds over B, scaled to the trimmed alpha.
    var pm = [0.0, 0.0, 0.0]
    for c in 0..<3 {
        let C = Double(px[p * 4 + c])
        pm[c] = raw > 0 ? min(max((C - B[c]) * (al / raw) + al * B[c], 0), 255 * al) : 0
        dark[p * 4 + c] = UInt8(pm[c].rounded())
    }
    dark[p * 4 + 3] = UInt8((al * 255).rounded())
    // Tinted: the drawing over black as gray; value and luminance mixed, so the violet glass is as present as
    // the white body.
    let v = pm.max()!, l = 0.2126 * pm[0] + 0.7152 * pm[1] + 0.0722 * pm[2]
    let g = min(255, pow((0.55 * v + 0.45 * l) / 255, 0.85) * 255)
    for c in 0..<3 { gray[p * 4 + c] = UInt8(g.rounded()) }
    gray[p * 4 + 3] = 255
}
func image(_ buf: inout [UInt8], _ info: UInt32) -> CGImage {
    CGContext(data: &buf, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: cs, bitmapInfo: info)!.makeImage()!
}
let darkImg = image(&dark, CGImageAlphaInfo.premultipliedLast.rawValue)
let grayImg = image(&gray, CGImageAlphaInfo.noneSkipLast.rawValue)
func save(_ img: CGImage, _ name: String) {
    let d = CGImageDestinationCreateWithURL(out.appendingPathComponent(name) as CFURL, UTType.png.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(d, img, nil); CGImageDestinationFinalize(d)
}
save(darkImg, "AppIcon-Dark-1024.png")
// The tinted icon goes in as grayscale: redraw into a gray colour space.
let gctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpaceCreateDeviceGray(),
                     bitmapInfo: CGImageAlphaInfo.none.rawValue)!
gctx.draw(grayImg, in: CGRect(x: 0, y: 0, width: w, height: h))
save(gctx.makeImage()!, "AppIcon-Tinted-1024.png")

// ---- preview sheet
let W = 1500.0, H = 1000.0
let sheet = CGContext(data: nil, width: Int(W), height: Int(H), bitsPerComponent: 8, bytesPerRow: 0, space: cs,
                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
let wall = CGGradient(colorsSpace: cs, colors: [CGColor(srgbRed: 0.20, green: 0.15, blue: 0.47, alpha: 1),
                                               CGColor(srgbRed: 0.30, green: 0.24, blue: 0.60, alpha: 1)] as CFArray, locations: [0, 1])!
sheet.drawLinearGradient(wall, start: CGPoint(x: 0, y: 0), end: CGPoint(x: 0, y: H), options: [])
func shape(_ r: CGRect) -> CGPath { CGPath(roundedRect: r, cornerWidth: r.width * 0.2237, cornerHeight: r.width * 0.2237, transform: nil) }
// iOS's dark icon background, read off another app's dark icon on an iOS 27 home screen: about 32 at the top, 14 at
// the bottom.
func darkBG(_ r: CGRect, tint: (Double, Double, Double)? = nil, in ctx: CGContext = sheet) {
    let t = tint ?? (1, 1, 1)
    let top = 32.0 / 255, bottom = 14.0 / 255
    let g = CGGradient(colorsSpace: cs, colors: [CGColor(srgbRed: bottom * t.0, green: bottom * t.1, blue: bottom * t.2, alpha: 1),
                                                CGColor(srgbRed: top * t.0, green: top * t.1, blue: top * t.2, alpha: 1)] as CFArray,
                       locations: [0, 1])!
    ctx.drawLinearGradient(g, start: CGPoint(x: 0, y: r.minY), end: CGPoint(x: 0, y: r.maxY), options: [])
}
let tint = (0.98, 0.62, 0.30)   // an orange home-screen tint, picked so the tinted preview reads as tinted
// The tinted icon as iOS shows it, roughly: the gray as the tint colour's strength over a tinted dark background.
var tinted = [UInt8](repeating: 0, count: w * h * 4)
for p in 0..<(w * h) {
    let g = Double(gray[p * 4]) / 255
    let c = [tint.0, tint.1, tint.2]
    for k in 0..<3 { tinted[p * 4 + k] = UInt8(min(255, (g * c[k]) * 255).rounded()) }
    tinted[p * 4 + 3] = UInt8((g * 255).rounded())
}
let tintedImg = image(&tinted, CGImageAlphaInfo.premultipliedLast.rawValue)
enum Mode { case light, dark, tinted, picture(CGImage) }
func icon(_ mode: Mode, _ r: CGRect) {
    sheet.saveGState(); sheet.addPath(shape(r)); sheet.clip(); sheet.interpolationQuality = .high
    switch mode {
    case .light: sheet.draw(image, in: r)
    case .dark: darkBG(r); sheet.draw(darkImg, in: r)
    case .tinted: darkBG(r, tint: (tint.0 * 0.9, tint.1 * 0.9, tint.2 * 0.9)); sheet.draw(tintedImg, in: r)
    case .picture(let img): sheet.draw(img, in: r)
    }
    sheet.restoreGState()
}
func label(_ s: String, _ x: Double, _ y: Double, _ size: Double = 30) {
    let attrs = [kCTFontAttributeName: CTFontCreateWithName("Helvetica-Bold" as CFString, size, nil),
                 kCTForegroundColorAttributeName: CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1)] as CFDictionary
    let line = CTLineCreateWithAttributedString(CFAttributedStringCreate(nil, s as CFString, attrs)!)
    let wd = CTLineGetTypographicBounds(line, nil, nil, nil)
    sheet.textPosition = CGPoint(x: x - wd / 2, y: y)
    CTLineDraw(line, sheet)
}
var cols: [(String, Mode)] = [("Light", .light), ("Dark", .dark), ("Tinted", .tinted)]
// An optional square image of another app's dark icon, to compare the background with.
if a.count > 3 { cols.insert(("Reference", .picture(load(URL(fileURLWithPath: a[3])))), at: 1) }
let colW = W / Double(cols.count)
for (i, (name, mode)) in cols.enumerated() {
    let cx = colW * (Double(i) + 0.5)
    label(name, cx, H - 70)
    icon(mode, CGRect(x: cx - 150, y: H - 120 - 300, width: 300, height: 300))
    icon(mode, CGRect(x: cx - 60, y: H - 120 - 300 - 70 - 120, width: 120, height: 120))
    icon(mode, CGRect(x: cx - 32, y: H - 120 - 300 - 70 - 120 - 50 - 64, width: 64, height: 64))
}
save(sheet.makeImage()!, "appearance-sheet.png")
// README icon: the dark icon on iOS's dark background inside the rounded shape, transparent outside it.
let R = 256.0
let readme = CGContext(data: nil, width: Int(R), height: Int(R), bitsPerComponent: 8, bytesPerRow: 0, space: cs,
                       bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
let rr = CGRect(x: 0, y: 0, width: R, height: R)
readme.addPath(shape(rr)); readme.clip(); readme.interpolationQuality = .high
darkBG(rr, in: readme)
readme.draw(darkImg, in: rr)
save(readme.makeImage()!, "readme-icon-256.png")
print("done")
