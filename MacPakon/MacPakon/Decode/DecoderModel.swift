import Foundation
import Combine
import CoreGraphics

@MainActor
final class DecoderModel: ObservableObject {

    enum State: Equatable {
        case idle
        case decoding(step: String, progress: Double)
        case ready
        case failed(String)
    }

    @Published var state: State = .idle
    @Published var frames: [DecodedFrame] = []
    @Published var rawURL: URL?

    private var ribbon: RGBRibbon?
    private var decodeTask: Task<Void, Never>?

    // MARK: - Load & decode

    func decode(url: URL, nFrames: Int = 36) {
        rawURL = url
        frames = []
        ribbon = nil
        state = .decoding(step: DecodeStep.loading.rawValue, progress: 0)

        decodeTask = Task.detached(priority: .userInitiated) { [weak self] in
            do {
                let result = try await Self.run(url: url, nFrames: nFrames) { step, pct in
                    Task { @MainActor [weak self] in
                        self?.state = .decoding(step: step.rawValue, progress: pct)
                    }
                }
                await MainActor.run { [weak self] in
                    self?.ribbon  = result.ribbon
                    self?.frames  = result.frames
                    self?.state   = .ready
                }
            } catch {
                await MainActor.run { [weak self] in
                    self?.state = .failed(error.localizedDescription)
                }
            }
        }
    }

    private static nonisolated func run(url: URL, nFrames: Int,
                                        progress: @escaping (DecodeStep, Double) -> Void) async throws -> DecoderResult {
        progress(.loading, 0)
        let data = try Data(contentsOf: url, options: .mappedIfSafe)
        let raw  = PakonRaw(url: url, data: data)
        let dec  = PakonDecoder(raw: raw)

        progress(.deinterleave, 0.05)
        let chans = dec.deinterleave()

        progress(.irBand, 0.20)
        let ir = dec.findIRBand(chans: chans)

        let zones: [(col0: Int, col1: Int)]
        if let ir {
            let W = chans.width
            zones = [(col0: ir.col1 + 1, col1: W), (col0: 0, col1: ir.col0)]
                .filter { $0.col1 > $0.col0 }
        } else {
            zones = [(col0: 0, col1: chans.width)]
        }

        progress(.registering, 0.30)
        let leadsPerZone = zones.map { dec.measureLeads(chans: chans, col0: $0.col0, col1: $0.col1) }

        progress(.assembling, 0.55)
        let ribbon = dec.assembleRGB(chans: chans, zones: zones, leadsPerZone: leadsPerZone)

        progress(.autocrop, 0.65)
        let (cropped, _) = dec.autocrop(ribbon)

        progress(.splitting, 0.70)
        let boundaries = dec.findFrameBoundaries(ribbon: cropped, nFrames: nFrames)
        let splitRows  = [0] + boundaries + [cropped.height]
        var regions: [FrameRegion] = (0 ..< nFrames).map { i in
            FrameRegion(row0: splitRows[i], row1: splitRows[i + 1],
                        col0: 0, col1: cropped.width)
        }

        progress(.thumbnails, 0.80)
        var decoded: [DecodedFrame] = []
        for (i, region) in regions.enumerated() {
            let thumb = dec.makeFrameThumbnail(cropped, region: region, maxDim: 600)
            decoded.append(DecodedFrame(index: i, region: region, thumbnail: thumb, ribbon: cropped))
            progress(.thumbnails, 0.80 + 0.20 * Double(i + 1) / Double(regions.count))
        }

        progress(.done, 1.0)
        return DecoderResult(ribbon: cropped, frames: decoded)
    }

    // MARK: - Export

    func exportFrame(_ frame: DecodedFrame, to url: URL) async throws {
        guard let ribbon else { return }
        let dec = PakonDecoder(raw: PakonRaw(url: rawURL!, data: Data()))
        guard let img = dec.makeCGImage16(ribbon: ribbon, region: frame.region) else {
            throw ExportError.imageCreationFailed
        }
        try TIFFExporter.write(image: img, to: url)
    }

    func exportAll(to directory: URL) async {
        guard let ribbon else { return }
        let dec = PakonDecoder(raw: PakonRaw(url: rawURL ?? directory, data: Data()))
        for frame in frames {
            let name = String(format: "frame_%02d.tiff", frame.index + 1)
            let url  = directory.appendingPathComponent(name)
            guard let img = dec.makeCGImage16(ribbon: ribbon, region: frame.region) else { continue }
            try? TIFFExporter.write(image: img, to: url)
        }
    }
}

// MARK: - Supporting types

struct DecoderResult {
    let ribbon: RGBRibbon
    let frames: [DecodedFrame]
}

final class DecodedFrame: Identifiable {
    let id = UUID()
    let index: Int
    var region: FrameRegion      // mutable for user crop adjustments
    let thumbnail: CGImage?
    let ribbon: RGBRibbon

    init(index: Int, region: FrameRegion, thumbnail: CGImage?, ribbon: RGBRibbon) {
        self.index     = index
        self.region    = region
        self.thumbnail = thumbnail
        self.ribbon    = ribbon
    }
}

enum ExportError: LocalizedError {
    case imageCreationFailed
    case writeFailed
    var errorDescription: String? {
        switch self {
        case .imageCreationFailed: return "Could not create 16-bit image"
        case .writeFailed:         return "Failed to write TIFF"
        }
    }
}
