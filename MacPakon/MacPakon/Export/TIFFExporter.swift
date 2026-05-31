import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

enum TIFFExporter {
    static func write(image: CGImage, to url: URL) throws {
        guard let dest = CGImageDestinationCreateWithURL(
            url as CFURL,
            UTType.tiff.identifier as CFString,
            1, nil)
        else { throw ExportError.writeFailed }

        CGImageDestinationAddImage(dest, image, nil)
        guard CGImageDestinationFinalize(dest) else { throw ExportError.writeFailed }
    }
}
