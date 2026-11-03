import CoreGraphics
import CoreImage
import Foundation
import Vision

/// Reads QR codes that are packed edge to edge with no white border (the hunt posters), which
/// ordinary readers can't: find the codes' corner squares ourselves, cut each code out along them,
/// put it on white, and let Vision read the clean copy.
enum TileReader {
    struct Finder { var x: Double; var y: Double; var module: Double; var hits: Int }

    /// Greyscale pixels, 0 dark ... 255 light.
    struct Gray {
        let w: Int, h: Int
        var px: [UInt8]
        init(_ img: CGImage) {
            w = img.width; h = img.height
            px = [UInt8](repeating: 255, count: w * h)
            px.withUnsafeMutableBytes { buf in
                let ctx = CGContext(data: buf.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                                    space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)!
                ctx.draw(img, in: CGRect(x: 0, y: 0, width: w, height: h))
            }
        }
    }

    /// Dark or light, against the average brightness nearby (copes with glare and shadow).
    static func binarize(_ g: Gray, radius: Int) -> [Bool] {
        let w = g.w, h = g.h
        var integral = [Int](repeating: 0, count: (w + 1) * (h + 1))
        for y in 0..<h {
            var row = 0
            for x in 0..<w {
                row += Int(g.px[y * w + x])
                integral[(y + 1) * (w + 1) + x + 1] = integral[y * (w + 1) + x + 1] + row
            }
        }
        var dark = [Bool](repeating: false, count: w * h)
        for y in 0..<h {
            let y0 = max(0, y - radius), y1 = min(h, y + radius + 1)
            for x in 0..<w {
                let x0 = max(0, x - radius), x1 = min(w, x + radius + 1)
                let sum = integral[y1 * (w + 1) + x1] - integral[y0 * (w + 1) + x1] - integral[y1 * (w + 1) + x0] + integral[y0 * (w + 1) + x0]
                let mean = sum / ((x1 - x0) * (y1 - y0))
                dark[y * w + x] = Int(g.px[y * w + x]) < mean - 6
            }
        }
        return dark
    }

    /// Five runs dark, light, dark, light, dark in the ratio 1:1:3:1:1: a corner square, crossed through its middle.
    static func ratioOK(_ r: [Int]) -> Double? {
        let total = r.reduce(0, +)
        guard total >= 7 else { return nil }
        let m = Double(total) / 7
        let tol = m * 0.7
        guard abs(Double(r[0]) - m) < tol, abs(Double(r[1]) - m) < tol, abs(Double(r[2]) - 3 * m) < 3 * tol,
              abs(Double(r[3]) - m) < tol, abs(Double(r[4]) - m) < tol else { return nil }
        return m
    }

    /// Runs along a line through (x, y) in direction (dx, dy), centred on the dark run there.
    static func crossCheck(_ dark: [Bool], _ w: Int, _ h: Int, x: Int, y: Int, dx: Int, dy: Int, maxRun: Int) -> (Double, Double)? {
        func at(_ i: Int) -> Bool? {
            let px = x + i * dx, py = y + i * dy
            guard px >= 0, py >= 0, px < w, py < h else { return nil }
            return dark[py * w + px]
        }
        guard at(0) == true else { return nil }
        var counts = [0, 0, 0, 0, 0]
        var i = 0
        while at(i) == true { counts[2] += 1; i -= 1; if counts[2] > maxRun * 3 { return nil } }
        while at(i) == false { counts[1] += 1; i -= 1; if counts[1] > maxRun { return nil } }
        while at(i) == true { counts[0] += 1; i -= 1; if counts[0] > maxRun { return nil } }
        var j = 1
        while at(j) == true { counts[2] += 1; j += 1; if counts[2] > maxRun * 3 { return nil } }
        while at(j) == false { counts[3] += 1; j += 1; if counts[3] > maxRun { return nil } }
        while at(j) == true { counts[4] += 1; j += 1; if counts[4] > maxRun { return nil } }
        guard counts[1] > 0, counts[0] > 0, counts[3] > 0, counts[4] > 0, let m = ratioOK(counts) else { return nil }
        // where the middle of the centre run is, along this line
        let start = i + 1 + counts[0] + counts[1]
        return (Double(start) + Double(counts[2]) / 2 - 0.5, m)
    }

    static func finders(_ dark: [Bool], _ w: Int, _ h: Int, step: Int = 2) -> [Finder] {
        var found: [Finder] = []
        for y in stride(from: 0, to: h, by: step) {
            var runs: [Int] = [], starts: [Int] = []
            var x = 0
            while x < w {
                let v = dark[y * w + x]
                var n = 0
                while x < w && dark[y * w + x] == v { n += 1; x += 1 }
                if runs.isEmpty && !v { continue }   // start on dark
                runs.append(n); starts.append(x - n)
                guard runs.count >= 5 else { continue }
                if runs.count > 5 { runs.removeFirst(); starts.removeFirst() }
                // the window must start on dark: runs alternate, so check parity via the first start
                guard dark[y * w + starts[0]], let m = ratioOK(runs) else { continue }
                let cx = starts[2] + runs[2] / 2
                let maxRun = Int(m * 3) + 2
                guard let (cy, mv) = crossCheck(dark, w, h, x: cx, y: y, dx: 0, dy: 1, maxRun: maxRun) else { continue }
                let fy = y + Int(cy.rounded())
                guard let (cx2, mh) = crossCheck(dark, w, h, x: cx, y: fy, dx: 1, dy: 0, maxRun: maxRun),
                      crossCheck(dark, w, h, x: cx + Int(cx2.rounded()), y: fy, dx: 1, dy: 1, maxRun: maxRun * 2) != nil else { continue }
                let f = Finder(x: Double(cx) + cx2, y: Double(fy), module: (m + mv + mh) / 3, hits: 1)
                if let k = found.firstIndex(where: { abs($0.x - f.x) < f.module * 2 && abs($0.y - f.y) < f.module * 2 }) {
                    let o = found[k], n = Double(o.hits)
                    found[k] = Finder(x: (o.x * n + f.x) / (n + 1), y: (o.y * n + f.y) / (n + 1), module: (o.module * n + f.module) / (n + 1), hits: o.hits + 1)
                } else {
                    found.append(f)
                }
            }
        }
        return found.filter { $0.hits >= 2 }
    }

    /// Threes of corner squares that could be one code: a right angle, two equal sides, a real QR size.
    static func tiles(_ fs: [Finder]) -> [(CGPoint, CGPoint, CGPoint, Double, Double)] {
        var out: [(CGPoint, CGPoint, CGPoint, Double, Double)] = []   // top left, top right, bottom left, module, score
        for (ai, a) in fs.enumerated() {
            for (bi, b) in fs.enumerated() where bi != ai {
                for (ci, c) in fs.enumerated() where ci > bi && ci != ai {
                    let m = (a.module + b.module + c.module) / 3
                    guard max(a.module, b.module, c.module) < m * 1.4, min(a.module, b.module, c.module) > m * 0.7 else { continue }
                    let ab = (b.x - a.x, b.y - a.y), ac = (c.x - a.x, c.y - a.y)
                    let lab = (ab.0 * ab.0 + ab.1 * ab.1).squareRoot(), lac = (ac.0 * ac.0 + ac.1 * ac.1).squareRoot()
                    guard abs(lab - lac) < 0.12 * max(lab, lac) else { continue }
                    let modules = (lab + lac) / 2 / m
                    let size = modules + 7
                    guard size > 18, size < 60 else { continue }
                    let cos = (ab.0 * ac.0 + ab.1 * ac.1) / (lab * lac)
                    guard abs(cos) < 0.2 else { continue }
                    // A at the corner; B and C the right way round, or the code reads mirrored
                    let cross = ab.0 * ac.1 - ab.1 * ac.0
                    let (tr, bl) = cross > 0 ? (b, c) : (c, b)
                    let versionFit = abs((size - 17) / 4 - ((size - 17) / 4).rounded())
                    out.append((CGPoint(x: a.x, y: a.y), CGPoint(x: tr.x, y: tr.y), CGPoint(x: bl.x, y: bl.y), m, abs(cos) + versionFit + abs(lab - lac) / lab))
                }
            }
        }
        return out.sorted { $0.4 < $1.4 }
    }

    static let ci = CIContext(options: [.useSoftwareRenderer: false])

    /// Cut one code out along its corner squares, straighten it, and give it a white border.
    static func cut(_ image: CIImage, height: Double, tl: CGPoint, tr: CGPoint, bl: CGPoint, module m: Double) -> CGImage? {
        func unit(_ p: CGPoint, _ q: CGPoint) -> CGPoint { let d = hypot(q.x - p.x, q.y - p.y); return CGPoint(x: (q.x - p.x) / d, y: (q.y - p.y) / d) }
        let u = unit(tl, tr), v = unit(tl, bl), e = 3.5 * m
        func pt(_ p: CGPoint, _ su: Double, _ sv: Double) -> CGPoint { CGPoint(x: p.x + su * e * u.x + sv * e * v.x, y: p.y + su * e * u.y + sv * e * v.y) }
        let c1 = pt(tl, -1, -1), c2 = pt(tr, 1, -1), c3 = pt(bl, -1, 1)
        let c4 = CGPoint(x: c2.x + c3.x - c1.x, y: c2.y + c3.y - c1.y)
        func flip(_ p: CGPoint) -> CIVector { CIVector(x: p.x, y: height - p.y) }   // Core Image counts up from the bottom
        let straight = image.applyingFilter("CIPerspectiveCorrection", parameters: [
            "inputTopLeft": flip(c1), "inputTopRight": flip(c2), "inputBottomLeft": flip(c3), "inputBottomRight": flip(c4)])
        let side = 300.0
        let scaled = straight.transformed(by: CGAffineTransform(scaleX: side / straight.extent.width, y: side / straight.extent.height))
            .transformed(by: CGAffineTransform(translationX: -straight.extent.minX * side / straight.extent.width + 60,
                                               y: -straight.extent.minY * side / straight.extent.height + 60))
        let canvas = CIImage(color: .white).cropped(to: CGRect(x: 0, y: 0, width: side + 120, height: side + 120))
        return ci.createCGImage(scaled.composited(over: canvas), from: canvas.extent)
    }

    static func vision(_ img: CGImage) -> [String] {
        let r = VNDetectBarcodesRequest()
        r.symbologies = [.qr]
        try? VNImageRequestHandler(cgImage: img).perform([r])
        return (r.results ?? []).compactMap { $0.payloadStringValue }
    }

    /// The first code readable tile by tile, or nil. Ordinary codes are left to the camera's own reader.
    static func read(_ img: CGImage, tries: Int = 40) -> String? {
        let g = Gray(img)
        let dark = binarize(g, radius: max(8, min(g.w, g.h) / 60))
        let fs = finders(dark, g.w, g.h)
        let ciImage = CIImage(cgImage: img)
        for t in tiles(fs).prefix(tries) {
            guard let crop = cut(ciImage, height: Double(g.h), tl: t.0, tr: t.1, bl: t.2, module: t.3) else { continue }
            if let text = vision(crop).first { return text }
        }
        return nil
    }
}
