import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @StateObject private var model = ScannerModel()
    @State private var scriptURL: URL? = defaultScriptURL()
    @State private var outputDir: URL = defaultOutputDir()
    @State private var showScriptPicker = false
    @State private var showOutputDirPicker = false
    @State private var showDoneAlert = false
    @State private var doneURL: URL?
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(spacing: 20) {
            statusBadge
            if model.isCold {
                firmwareSection
            } else {
                outputDirRow
                Divider()
                actionButton
                progressRow
            }
        }
        .padding(24)
        .frame(minWidth: 420, minHeight: 260)
        .onAppear { model.startPolling() }
        .onDisappear { model.stopPolling() }
        .onChange(of: model.scanState) { _, state in
            if case .done(let url) = state {
                doneURL = url
                showDoneAlert = true
            }
        }
        .alert("Scan complete", isPresented: $showDoneAlert, presenting: doneURL) { url in
            Button("Process…") { openWindow(id: "processing") }
            Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([url]) }
            Button("OK", role: .cancel) {}
        } message: { url in
            Text(url.lastPathComponent)
        }
        .fileImporter(isPresented: $showScriptPicker,
                      allowedContentTypes: [.data],
                      allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first { scriptURL = url }
        }
        .fileImporter(isPresented: $showOutputDirPicker,
                      allowedContentTypes: [.folder],
                      allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first { outputDir = url }
        }
    }

    // ---- Status badge ----

    private var statusBadge: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(badgeColor)
                .frame(width: 10, height: 10)
            Text(badgeLabel)
                .foregroundStyle(model.connectionState == .warm ? .primary : .secondary)
            Spacer()
        }
    }

    private var badgeColor: Color {
        switch model.connectionState {
        case .warm:         return .green
        case .cold:         return .orange
        case .disconnected: return .gray
        }
    }

    private var badgeLabel: String {
        switch model.connectionState {
        case .warm:         return "Scanner connected"
        case .cold:         return "Scanner detected — needs firmware"
        case .disconnected: return "No scanner detected"
        }
    }

    // ---- Firmware section (cold state) ----

    private var firmwareSection: some View {
        VStack(spacing: 14) {
            switch model.firmwareState {
            case .idle:
                HStack {
                    Button("Load firmware") { model.loadFirmware() }
                        .buttonStyle(.borderedProminent)
                    Spacer()
                }

            case .loading(let pct):
                HStack(spacing: 8) {
                    ProgressView(value: pct)
                        .frame(maxWidth: 200)
                    Text("\(Int(pct * 100))%")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                }

            case .failed(let msg):
                VStack(alignment: .leading, spacing: 4) {
                    Text("Error: \(msg)").foregroundStyle(.red).font(.caption)
                    Button("Retry") { model.firmwareState = .idle }
                }
            }
        }
    }

    // ---- Scan rows ----

    private var scriptRow: some View {
        HStack {
            Text("Scan script:")
                .frame(width: 90, alignment: .trailing)
            Text(scriptURL?.lastPathComponent ?? "not set")
                .truncationMode(.middle)
                .lineLimit(1)
                .foregroundStyle(scriptURL == nil ? .red : .primary)
            Spacer()
            Button("Choose…") { showScriptPicker = true }
                .disabled(model.scanState == .running)
        }
    }

    private var outputDirRow: some View {
        HStack {
            Text("Output folder:")
                .frame(width: 90, alignment: .trailing)
            Text(outputDir.lastPathComponent)
                .truncationMode(.middle)
                .lineLimit(1)
            Spacer()
            Button("Choose…") { showOutputDirPicker = true }
                .disabled(model.scanState == .running)
        }
    }

    private var actionButton: some View {
        HStack {
            switch model.scanState {
            case .idle:
                Button("Scan") {
                    guard let script = scriptURL else { return }
                    model.startScan(scriptURL: script, outputDir: outputDir)
                }
                .buttonStyle(.borderedProminent)
                .disabled(!model.canScan || scriptURL == nil)

            case .running:
                Button("Cancel", role: .destructive) { model.cancel() }
                    .buttonStyle(.bordered)

            case .done:
                Button("Scan again") { model.scanState = .idle }
                    .buttonStyle(.bordered)

            case .failed(let msg):
                VStack(alignment: .leading) {
                    Text("Error: \(msg)").foregroundStyle(.red).font(.caption)
                    Button("Retry") { model.scanState = .idle }
                }
            }
            Spacer()
        }
    }

    private var progressRow: some View {
        HStack {
            if model.scanState == .running {
                ProgressView()
                    .scaleEffect(0.7)
                    .frame(width: 16, height: 16)
                Text("Scanning… \(model.formattedBytes) received")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else if case .done = model.scanState {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                Text("Done — \(model.formattedBytes) captured")
                    .font(.caption)
            }
            Spacer()
        }
        .frame(height: 20)
    }
}

// ---- Defaults ----

private func defaultScriptURL() -> URL? {
    Bundle.main.url(forResource: "36frames", withExtension: "pakscan")
}

private func defaultOutputDir() -> URL {
    FileManager.default.urls(for: .picturesDirectory, in: .userDomainMask).first
        ?? FileManager.default.temporaryDirectory
}

#Preview {
    ContentView()
}
