import SwiftUI

struct FrameCardView: View {
    let frame: DecodedFrame
    let onEdit: () -> Void
    let onExport: () -> Void

    var body: some View {
        VStack(spacing: 6) {
            ZStack(alignment: .bottomTrailing) {
                if let cg = frame.thumbnail {
                    Image(cg, scale: 1, label: Text("Frame \(frame.index + 1)"))
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .cornerRadius(4)
                } else {
                    RoundedRectangle(cornerRadius: 4)
                        .fill(Color.gray.opacity(0.2))
                        .aspectRatio(3/2, contentMode: .fit)
                }
            }

            HStack {
                Text("Frame \(frame.index + 1)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Crop") { onEdit() }
                    .font(.caption)
                Button("Export") { onExport() }
                    .font(.caption)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
            }
        }
        .padding(8)
        .background(Color(nsColor: .controlBackgroundColor))
        .cornerRadius(8)
    }
}
