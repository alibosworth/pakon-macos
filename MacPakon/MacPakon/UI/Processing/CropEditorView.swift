import SwiftUI

/// Shows a frame thumbnail with an adjustable crop rectangle.
/// The user drags the edges to refine the auto-suggested crop.
struct CropEditorView: View {
    let frame: DecodedFrame
    let model: DecoderModel

    @State private var cropRect: CGRect = .zero   // normalised 0..1 within the image
    @State private var imageSize: CGSize = .zero
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            Text("Frame \(frame.index + 1) — adjust crop")
                .font(.headline)
                .padding(.vertical, 12)

            Divider()

            GeometryReader { geo in
                ZStack(alignment: .topLeading) {
                    if let cg = frame.thumbnail {
                        Image(cg, scale: 1, label: Text(""))
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .onAppear {
                                imageSize = geo.size
                                initCropRect(geo: geo, cg: cg)
                            }
                    }

                    CropOverlay(rect: $cropRect, containerSize: geo.size)
                }
            }
            .padding(16)

            Divider()

            HStack {
                Button("Reset") { if let cg = frame.thumbnail { initCropRect(geo: nil, cg: cg) } }
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Apply") {
                    applyUserCrop()
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
            }
            .padding(12)
        }
        .frame(minWidth: 700, minHeight: 520)
    }

    private func initCropRect(geo: GeometryProxy?, cg: CGImage) {
        // Default: full frame
        cropRect = CGRect(x: 0, y: 0, width: 1, height: 1)
    }

    private func applyUserCrop() {
        let r = frame.region
        let newRow0 = r.row0 + Int(cropRect.minY * Double(r.rowCount))
        let newRow1 = r.row0 + Int(cropRect.maxY * Double(r.rowCount))
        let newCol0 = r.col0 + Int(cropRect.minX * Double(r.colCount))
        let newCol1 = r.col0 + Int(cropRect.maxX * Double(r.colCount))
        frame.region = FrameRegion(row0: max(r.row0, newRow0), row1: min(r.row1, newRow1),
                                   col0: max(r.col0, newCol0), col1: min(r.col1, newCol1))
    }
}

// MARK: - Crop overlay with draggable handles

struct CropOverlay: View {
    @Binding var rect: CGRect
    let containerSize: CGSize

    var body: some View {
        ZStack {
            // Dimmed areas outside crop
            Path { p in
                p.addRect(CGRect(origin: .zero, size: containerSize))
                p.addRect(displayRect)
            }
            .fill(style: FillStyle(eoFill: true))
            .foregroundStyle(Color.black.opacity(0.45))

            // Crop border
            Rectangle()
                .stroke(Color.white, lineWidth: 1.5)
                .frame(width: displayRect.width, height: displayRect.height)
                .position(x: displayRect.midX, y: displayRect.midY)

            // Corner handles
            ForEach(Handle.allCases, id: \.self) { handle in
                Circle()
                    .fill(Color.white)
                    .frame(width: 14, height: 14)
                    .shadow(radius: 2)
                    .position(handlePos(handle))
                    .gesture(DragGesture()
                        .onChanged { v in dragHandle(handle, by: v.translation) })
            }
        }
    }

    private var displayRect: CGRect {
        CGRect(x: rect.minX * containerSize.width,
               y: rect.minY * containerSize.height,
               width: rect.width * containerSize.width,
               height: rect.height * containerSize.height)
    }

    private func handlePos(_ h: Handle) -> CGPoint {
        let r = displayRect
        switch h {
        case .topLeft:     return CGPoint(x: r.minX, y: r.minY)
        case .topRight:    return CGPoint(x: r.maxX, y: r.minY)
        case .bottomLeft:  return CGPoint(x: r.minX, y: r.maxY)
        case .bottomRight: return CGPoint(x: r.maxX, y: r.maxY)
        }
    }

    private func dragHandle(_ h: Handle, by t: CGSize) {
        let dx = t.width  / containerSize.width
        let dy = t.height / containerSize.height
        var r = rect
        switch h {
        case .topLeft:
            r.origin.x = min(r.maxX - 0.05, r.origin.x + dx)
            r.origin.y = min(r.maxY - 0.05, r.origin.y + dy)
            r.size.width  -= dx; r.size.height -= dy
        case .topRight:
            r.size.width  = max(0.05, r.size.width + dx)
            r.origin.y    = min(r.maxY - 0.05, r.origin.y + dy)
            r.size.height -= dy
        case .bottomLeft:
            r.origin.x = min(r.maxX - 0.05, r.origin.x + dx)
            r.size.width  -= dx
            r.size.height = max(0.05, r.size.height + dy)
        case .bottomRight:
            r.size.width  = max(0.05, r.size.width + dx)
            r.size.height = max(0.05, r.size.height + dy)
        }
        r.origin.x = max(0, min(r.origin.x, 1 - r.width))
        r.origin.y = max(0, min(r.origin.y, 1 - r.height))
        rect = r
    }

    enum Handle: CaseIterable { case topLeft, topRight, bottomLeft, bottomRight }
}
