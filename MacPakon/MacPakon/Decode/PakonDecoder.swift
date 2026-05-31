import Accelerate
import CoreGraphics

// All methods are nonisolated; call from a background task.
struct PakonDecoder {

    let raw: PakonRaw

    // MARK: - Deinterleave

    func deinterleave() -> PakonChannels {
        let W  = raw.channelWidth
        let H  = raw.lineCount
        let LW = raw.lineWidth
        var r = [UInt16](repeating: 0, count: H * W)
        var g = [UInt16](repeating: 0, count: H * W)
        var b = [UInt16](repeating: 0, count: H * W)

        raw.data.withUnsafeBytes { ptr in
            guard let base = ptr.baseAddress?.assumingMemoryBound(to: UInt16.self) else { return }
            // Interleave order: position 0 = B, 1 = R, 2 = G
            for row in 0 ..< H {
                let src = base + row * LW
                let dst = row * W
                for col in 0 ..< W {
                    b[dst + col] = src[col * 3]
                    r[dst + col] = src[col * 3 + 1]
                    g[dst + col] = src[col * 3 + 2]
                }
            }
        }

        // Sample full-scale from 1-in-1000 rows to avoid a full pass.
        var fullScale: Float = 1.0
        for ch in [r, g, b] {
            for row in stride(from: 0, to: H, by: max(1, H / 200)) {
                let slice = ch[row * W ..< min(row * W + W, ch.count)]
                let mx = slice.max() ?? 0
                if Float(mx) > fullScale { fullScale = Float(mx) }
            }
        }

        return PakonChannels(r: r, g: g, b: b, width: W, height: H, fullScale: fullScale)
    }

    // MARK: - IR band detection

    func findIRBand(chans: PakonChannels) -> IRBand? {
        let W = chans.width, H = chans.height
        let a = max(0, H / 2 - 5000), b = min(H, H / 2 + 5000)
        let sliceH = b - a
        guard sliceH > 0 else { return nil }
        let full = chans.fullScale

        var colMean  = [Float](repeating: 0, count: W)
        var colSpread = [Float](repeating: 0, count: W)

        // Per-column: mean across the middle slice rows and channels,
        // and mean of per-row (max_ch - min_ch) to measure colour spread.
        for col in 0 ..< W {
            var sumAll: Float = 0
            var sumSpread: Float = 0
            for row in a ..< b {
                let rv = Float(chans.r[row * W + col])
                let gv = Float(chans.g[row * W + col])
                let bv = Float(chans.b[row * W + col])
                sumAll   += (rv + gv + bv) / 3.0
                sumSpread += max(rv, gv, bv) - min(rv, gv, bv)
            }
            colMean[col]   = sumAll   / Float(sliceH)
            colSpread[col] = sumSpread / Float(sliceH)
        }

        let irMask = (0 ..< W).map { colMean[$0] > 0.35 * full && colSpread[$0] < 0.06 * full }
        let runs = allRuns(irMask, minLen: 100)
        guard let best = runs.first else { return nil }
        return IRBand(col0: best.0, col1: best.1)
    }

    // MARK: - Trilinear registration

    func measureLeads(chans: PakonChannels, col0: Int, col1: Int, searchRange: Int = 40) -> TriLeads {
        TriLeads(r: 0,
                 g: channelLead(ch: chans.g, ref: chans.r, width: chans.width,
                                col0: col0, col1: col1, range: searchRange),
                 b: channelLead(ch: chans.b, ref: chans.r, width: chans.width,
                                col0: col0, col1: col1, range: searchRange))
    }

    private func channelLead(ch: [UInt16], ref: [UInt16], width: Int,
                              col0: Int, col1: Int, range: Int) -> Int {
        let H = ch.count / width
        let a = Int(Double(H) * 0.35)
        let b = Int(Double(H) * 0.65)
        let clampedRange = min(range, (b - a) / 4)
        let colStep = max(1, (col1 - col0) / 200)
        let rowStep = max(1, (b - a) / 500)

        // Collect subsampled float rows for ch and ref
        var bestDy = 0, bestCorr: Float = -2
        for dy in -clampedRange ... clampedRange {
            var dotXY: Float = 0, sumX: Float = 0, sumY: Float = 0
            var sumX2: Float = 0, sumY2: Float = 0
            var n: Float = 0
            for row in stride(from: a, to: b, by: rowStep) {
                let refRow = row + dy
                guard refRow >= 0 && refRow < H else { continue }
                for col in stride(from: col0, to: col1, by: colStep) {
                    let x = Float(ch[row * width + col])
                    let y = Float(ref[refRow * width + col])
                    sumX += x; sumY += y
                    sumX2 += x * x; sumY2 += y * y
                    dotXY += x * y
                    n += 1
                }
            }
            guard n > 0 else { continue }
            let covXY = dotXY / n - (sumX / n) * (sumY / n)
            let varX  = sumX2 / n - (sumX / n) * (sumX / n)
            let varY  = sumY2 / n - (sumY / n) * (sumY / n)
            let d = sqrt(varX * varY)
            if d > 0 {
                let corr = covXY / d
                if corr > bestCorr { bestCorr = corr; bestDy = dy }
            }
        }
        return bestDy
    }

    // MARK: - Assemble RGB

    func assembleRGB(chans: PakonChannels,
                     zones: [(col0: Int, col1: Int)],
                     leadsPerZone: [TriLeads]) -> RGBRibbon {
        let W = chans.width, H = chans.height
        guard !zones.isEmpty else { return RGBRibbon(pixels: [], width: 0, height: 0) }

        // Global lead span: rows to trim so all zones align
        let allLeads = leadsPerZone.flatMap { [$0.r, $0.g, $0.b] }
        let gmax = allLeads.max() ?? 0
        let gmin = allLeads.min() ?? 0
        let outH = H - (gmax - gmin)
        guard outH > 0 else { return RGBRibbon(pixels: [], width: 0, height: 0) }

        let outW = zones.map { $0.col1 - $0.col0 }.reduce(0, +)
        var pixels = [UInt16](repeating: 0, count: outH * outW * 3)

        var dstColBase = 0
        for (zone, leads) in zip(zones, leadsPerZone) {
            let zW = zone.col1 - zone.col0
            for dstRow in 0 ..< outH {
                let rRow = dstRow + gmax - leads.r
                let gRow = dstRow + gmax - leads.g
                let bRow = dstRow + gmax - leads.b
                for zCol in 0 ..< zW {
                    let srcCol = zone.col0 + zCol
                    let dst = (dstRow * outW + dstColBase + zCol) * 3
                    pixels[dst]     = chans.r[rRow * W + srcCol]
                    pixels[dst + 1] = chans.g[gRow * W + srcCol]
                    pixels[dst + 2] = chans.b[bRow * W + srcCol]
                }
            }
            dstColBase += zW
        }

        return RGBRibbon(pixels: pixels, width: outW, height: outH)
    }

    // MARK: - Autocrop

    /// Returns (cropped ribbon, row/col offsets into the input).
    func autocrop(_ ribbon: RGBRibbon) -> (RGBRibbon, FrameRegion) {
        let W = ribbon.width, H = ribbon.height
        let full = Float(ribbon.pixels.max() ?? 1)
        let colStep = max(1, W / 400)

        var rowBright = [Float](repeating: 0, count: H)
        var rowSpread = [Float](repeating: 0, count: H)

        for row in 0 ..< H {
            var sum: Float = 0, maxV: Float = 0, minV: Float = Float.greatestFiniteMagnitude
            var count: Float = 0
            for col in stride(from: 0, to: W, by: colStep) {
                let base = (row * W + col) * 3
                let rv = Float(ribbon.pixels[base]), gv = Float(ribbon.pixels[base + 1]), bv = Float(ribbon.pixels[base + 2])
                let mean = (rv + gv + bv) / 3
                sum += mean
                let mx = max(rv, gv, bv), mn = min(rv, gv, bv)
                if mx > maxV { maxV = mx }
                if mn < minV { minV = mn }
                count += 1
            }
            rowBright[row] = count > 0 ? sum / count : 0
            rowSpread[row] = maxV - minV
        }

        let dark  = rowBright.map { $0 < 0.06 * full }
        let blank = zip(rowBright, rowSpread).map { $0 > 0.55 * full && $1 < 0.03 * full }
        let imageRow = zip(dark, blank).map { !$0 && !$1 }
        guard let (r0, r1) = allRuns(imageRow, minLen: 10).first else {
            return (ribbon, FrameRegion(row0: 0, row1: H, col0: 0, col1: W))
        }

        // Column trim: keep columns with meaningful detail
        let rowStep = max(1, (r1 - r0) / 200)
        var colStd = [Float](repeating: 0, count: W)
        for col in 0 ..< W {
            var sum: Float = 0, sumSq: Float = 0
            var n: Float = 0
            for row in stride(from: r0, to: r1, by: rowStep) {
                let base = (row * W + col) * 3
                let lum = (Float(ribbon.pixels[base]) + Float(ribbon.pixels[base+1]) + Float(ribbon.pixels[base+2])) / 3
                sum += lum; sumSq += lum * lum; n += 1
            }
            colStd[col] = n > 1 ? sqrt(max(0, sumSq/n - (sum/n)*(sum/n))) : 0
        }
        let maxStd = colStd.max() ?? 1
        let activeCol = colStd.map { $0 > 0.28 * maxStd }
        let (c0, c1) = allRuns(activeCol, minLen: 10).first ?? (0, W - 1)

        return (crop(ribbon, row0: r0, row1: r1 + 1, col0: c0, col1: c1 + 1),
                FrameRegion(row0: r0, row1: r1 + 1, col0: c0, col1: c1 + 1))
    }

    private func crop(_ ribbon: RGBRibbon, row0: Int, row1: Int, col0: Int, col1: Int) -> RGBRibbon {
        let outW = col1 - col0, outH = row1 - row0
        var out = [UInt16](repeating: 0, count: outH * outW * 3)
        for row in 0 ..< outH {
            let srcRow = row0 + row
            for col in 0 ..< outW {
                let src = (srcRow * ribbon.width + col0 + col) * 3
                let dst = (row * outW + col) * 3
                out[dst] = ribbon.pixels[src]
                out[dst + 1] = ribbon.pixels[src + 1]
                out[dst + 2] = ribbon.pixels[src + 2]
            }
        }
        return RGBRibbon(pixels: out, width: outW, height: outH)
    }

    // MARK: - Frame boundary detection

    func findFrameBoundaries(ribbon: RGBRibbon, nFrames: Int) -> [Int] {
        guard nFrames > 1 else { return [] }
        let W = ribbon.width, H = ribbon.height
        let rowStep = max(1, H / 4000)
        let colStep = max(1, W / 200)

        var detail = [Float](repeating: 0, count: H)
        for row in stride(from: 0, to: H, by: rowStep) {
            var sum: Float = 0, sumSq: Float = 0
            var n: Float = 0
            for col in stride(from: 0, to: W, by: colStep) {
                let base = (row * W + col) * 3
                let lum = (Float(ribbon.pixels[base]) + Float(ribbon.pixels[base+1]) + Float(ribbon.pixels[base+2])) / 3
                sum += lum; sumSq += lum * lum; n += 1
            }
            detail[row] = n > 1 ? sqrt(max(0, sumSq/n - (sum/n)*(sum/n))) : 0
        }

        // Box-smooth ~2% of ribbon height
        let win = max(5, H / 50)
        var smoothed = [Float](repeating: 0, count: H)
        for i in 0 ..< H {
            let lo = max(0, i - win / 2), hi = min(H, i + win / 2)
            var s: Float = 0
            for j in lo ..< hi { s += detail[j] }
            smoothed[i] = s / Float(hi - lo)
        }

        let expected = H / nFrames
        let halfWin  = max(expected / 4, 10)
        return (1 ..< nFrames).map { i in
            let center = i * expected
            let lo = max(0, center - halfWin), hi = min(H, center + halfWin)
            var minVal = Float.greatestFiniteMagnitude, minIdx = lo
            for j in lo ..< hi {
                if smoothed[j] < minVal { minVal = smoothed[j]; minIdx = j }
            }
            return minIdx
        }
    }

    // MARK: - CGImage helpers

    func makeThumbnail(_ ribbon: RGBRibbon, maxDim: Int = 800) -> CGImage? {
        let step = max(1, max(ribbon.width / maxDim, ribbon.height / maxDim))
        let tw = ribbon.width / step, th = ribbon.height / step
        guard tw > 0, th > 0 else { return nil }
        var rgb8 = [UInt8](repeating: 0, count: tw * th * 3)
        for row in 0 ..< th {
            for col in 0 ..< tw {
                let src = (row * step * ribbon.width + col * step) * 3
                let dst = (row * tw + col) * 3
                rgb8[dst]     = UInt8(ribbon.pixels[src]     >> 8)
                rgb8[dst + 1] = UInt8(ribbon.pixels[src + 1] >> 8)
                rgb8[dst + 2] = UInt8(ribbon.pixels[src + 2] >> 8)
            }
        }
        return makeCGImage8(rgb8: rgb8, width: tw, height: th)
    }

    func makeFrameThumbnail(_ ribbon: RGBRibbon, region: FrameRegion, maxDim: Int = 600) -> CGImage? {
        let fW = region.colCount, fH = region.rowCount
        let step = max(1, max(fW / maxDim, fH / maxDim))
        let tw = fW / step, th = fH / step
        guard tw > 0, th > 0 else { return nil }
        var rgb8 = [UInt8](repeating: 0, count: tw * th * 3)
        for row in 0 ..< th {
            let srcRow = region.row0 + row * step
            for col in 0 ..< tw {
                let srcCol = region.col0 + col * step
                let src = (srcRow * ribbon.width + srcCol) * 3
                let dst = (row * tw + col) * 3
                rgb8[dst]     = UInt8(ribbon.pixels[src]     >> 8)
                rgb8[dst + 1] = UInt8(ribbon.pixels[src + 1] >> 8)
                rgb8[dst + 2] = UInt8(ribbon.pixels[src + 2] >> 8)
            }
        }
        return makeCGImage8(rgb8: rgb8, width: tw, height: th)
    }

    private func makeCGImage8(rgb8: [UInt8], width: Int, height: Int) -> CGImage? {
        let data = Data(rgb8) as CFData
        guard let provider = CGDataProvider(data: data) else { return nil }
        return CGImage(width: width, height: height,
                       bitsPerComponent: 8, bitsPerPixel: 24,
                       bytesPerRow: width * 3,
                       space: CGColorSpace(name: CGColorSpace.sRGB)!,
                       bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
                       provider: provider, decode: nil,
                       shouldInterpolate: true, intent: .defaultIntent)
    }

    func makeCGImage16(ribbon: RGBRibbon, region: FrameRegion) -> CGImage? {
        let W = region.colCount, H = region.rowCount
        var buf = [UInt16](repeating: 0, count: W * H * 3)
        for row in 0 ..< H {
            let srcRow = region.row0 + row
            for col in 0 ..< W {
                let src = (srcRow * ribbon.width + region.col0 + col) * 3
                let dst = (row * W + col) * 3
                buf[dst] = ribbon.pixels[src]
                buf[dst + 1] = ribbon.pixels[src + 1]
                buf[dst + 2] = ribbon.pixels[src + 2]
            }
        }
        let data = buf.withUnsafeBytes { Data($0) } as CFData
        guard let provider = CGDataProvider(data: data) else { return nil }
        return CGImage(width: W, height: H,
                       bitsPerComponent: 16, bitsPerPixel: 48,
                       bytesPerRow: W * 6,
                       space: CGColorSpace(name: CGColorSpace.linearSRGB)!,
                       bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue | CGBitmapInfo.byteOrder16Little.rawValue),
                       provider: provider, decode: nil,
                       shouldInterpolate: false, intent: .defaultIntent)
    }

    // MARK: - Utilities

    private func allRuns(_ mask: [Bool], minLen: Int) -> [(Int, Int)] {
        var runs: [(Int, Int)] = []
        var start: Int? = nil
        for (i, v) in mask.enumerated() {
            if v && start == nil { start = i }
            if !v, let s = start { if i - s >= minLen { runs.append((s, i - 1)) }; start = nil }
        }
        if let s = start, mask.count - s >= minLen { runs.append((s, mask.count - 1)) }
        return runs.sorted { ($0.1 - $0.0) > ($1.1 - $1.0) }
    }
}
