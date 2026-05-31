import SwiftUI
import UniformTypeIdentifiers

struct ProcessingView: View {
    @StateObject private var model = DecoderModel()
    @State private var showFilePicker = false
    @State private var showExportPicker = false
    @State private var frameCount = 36
    @State private var selectedFrame: DecodedFrame?
    @State private var showCropEditor = false

    var body: some View {
        Group {
            switch model.state {
            case .idle:
                idleView
            case .decoding(let step, let pct):
                decodingView(step: step, progress: pct)
            case .ready:
                framesView
            case .failed(let msg):
                errorView(msg)
            }
        }
        .frame(minWidth: 800, minHeight: 500)
        .fileImporter(isPresented: $showFilePicker,
                      allowedContentTypes: [.data],
                      allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first {
                model.decode(url: url, nFrames: frameCount)
            }
        }
        .fileImporter(isPresented: $showExportPicker,
                      allowedContentTypes: [.folder],
                      allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let dir = urls.first {
                Task { await model.exportAll(to: dir) }
            }
        }
        .sheet(isPresented: $showCropEditor) {
            if let frame = selectedFrame {
                CropEditorView(frame: frame, model: model)
            }
        }
    }

    // ---- Idle ----

    private var idleView: some View {
        VStack(spacing: 20) {
            Text("Open a .raw scan file to preview and export frames.")
                .foregroundStyle(.secondary)
            HStack {
                Stepper("Frames: \(frameCount)", value: $frameCount, in: 1...72)
                    .frame(width: 160)
                Button("Open raw file…") { showFilePicker = true }
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(40)
    }

    // ---- Decoding ----

    private func decodingView(step: String, progress: Double) -> some View {
        VStack(spacing: 16) {
            Text(step).foregroundStyle(.secondary)
            ProgressView(value: progress)
                .frame(maxWidth: 320)
            Text("\(Int(progress * 100))%").font(.caption).foregroundStyle(.secondary)
        }
        .padding(40)
    }

    // ---- Frames grid ----

    private var framesView: some View {
        VStack(spacing: 0) {
            toolbar
            Divider()
            ScrollView {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), spacing: 12)], spacing: 12) {
                    ForEach(model.frames) { frame in
                        FrameCardView(frame: frame) {
                            selectedFrame = frame
                            showCropEditor = true
                        } onExport: {
                            exportSingle(frame)
                        }
                    }
                }
                .padding(16)
            }
        }
    }

    private var toolbar: some View {
        HStack(spacing: 12) {
            Button("Open another…") { showFilePicker = true }
            Spacer()
            if let url = model.rawURL {
                Text(url.lastPathComponent)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Export all…") { showExportPicker = true }
                .buttonStyle(.borderedProminent)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    // ---- Error ----

    private func errorView(_ msg: String) -> some View {
        VStack(spacing: 12) {
            Text("Decode failed").font(.headline)
            Text(msg).foregroundStyle(.secondary).font(.caption)
            Button("Try again") { model.state = .idle }
        }
        .padding(40)
    }

    // ---- Single export ----

    private func exportSingle(_ frame: DecodedFrame) {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.tiff]
        panel.nameFieldStringValue = String(format: "frame_%02d.tiff", frame.index + 1)
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            Task {
                do {
                    try await model.exportFrame(frame, to: url)
                } catch {
                    // Surface in UI if needed
                }
            }
        }
    }
}
