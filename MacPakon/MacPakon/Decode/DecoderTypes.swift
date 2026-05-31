import Foundation
import CoreGraphics

// ---- Raw file wrapper ----

struct PakonRaw {
    let url: URL
    let data: Data          // memory-mapped
    let lineWidth = 8000    // samples per scan line
    var sampleCount: Int  { data.count / 2 }
    var lineCount: Int    { sampleCount / lineWidth }
    var channelWidth: Int { lineWidth / 3 }   // 2666 px
}

// ---- Deinterleaved channels ----

/// Flat row-major arrays: index = row * channelWidth + col
struct PakonChannels {
    let r, g, b: [UInt16]
    let width: Int    // channelWidth = 2666
    let height: Int   // lineCount
    var fullScale: Float   // sampled max across all channels
}

// ---- IR band ----

struct IRBand {
    let col0, col1: Int   // in channel-column space
}

// ---- Trilinear registration leads ----

struct TriLeads {
    var r: Int = 0
    var g: Int = 0
    var b: Int = 0
}

// ---- Assembled RGB ribbon ----

/// Row-major, packed R,G,B per pixel (UInt16 × 3).
struct RGBRibbon {
    let pixels: [UInt16]
    let width: Int
    let height: Int

    func pixel(row: Int, col: Int) -> (r: UInt16, g: UInt16, b: UInt16) {
        let base = (row * width + col) * 3
        return (pixels[base], pixels[base + 1], pixels[base + 2])
    }
}

// ---- Frame region ----

struct FrameRegion: Identifiable {
    let id = UUID()
    var row0, row1: Int    // in ribbon space (after autocrop)
    var col0, col1: Int

    var rowCount: Int { row1 - row0 }
    var colCount: Int { col1 - col0 }
}

// ---- Decode progress ----

enum DecodeStep: String {
    case loading    = "Loading raw file…"
    case deinterleave = "Deinterleaving channels…"
    case irBand     = "Finding IR band…"
    case registering = "Registering trilinear channels…"
    case assembling = "Assembling image…"
    case autocrop   = "Auto-cropping…"
    case splitting  = "Finding frame boundaries…"
    case thumbnails = "Generating previews…"
    case done       = "Done"
}
