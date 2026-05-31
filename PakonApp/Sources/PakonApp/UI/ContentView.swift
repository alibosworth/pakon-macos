import SwiftUI

struct ContentView: View {
    @StateObject private var model = ScannerModel()
    @State private var scriptURL: URL? = defaultScriptURL()
    @State private var outputDir: URL = defaultOutputDir()
    @State private var showScriptPicker = false
    @State private var showOutputDirPicker = false
    @State private var showDoneAlert = false
    @State private var doneURL: URL?

    var body: some View {
        VStack(spacing: 20) {
            statusBadge
            scriptRow
            outputDirRow
            Divider()
            actionButton
            progressRow
        }
        .padding(24)
        .frame(minWidth: 420, minHeight: 260)
        .onAppear { model.startPolling() }
        .onDisappear { model.stopPolling() }
        .onChange(of: model.scanState) { state in
            if case .done(let url) = state {
                doneURL = url
                showDoneAlert = true
            }
        }
        .alert("Scan complete", isPresented: $showDoneAlert, presenting: doneURL) { url in
            Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([url]) }
            Button("OK", role: .cancel) {}
        } message: { url in
            Text(url.lastPathComponent)
        }
        .fileImporter(isPresented: $showScriptPicker,
                      allowedContentTypes: [.data],
                      allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first {
                scriptURL = url
            }
        }
        .fileImporter(isPresented: $showOutputDirPicker,
                      allowedContentTypes: [.folder],
                      allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first {
                outputDir = url
            }
        }
    }

    // ---- Subviews ----

    private var statusBadge: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(model.isConnected ? Color.green : Color.gray)
                .frame(width: 10, height: 10)
            Text(model.isConnected ? "Scanner connected" : "No scanner detected")
                .foregroundStyle(model.isConnected ? .primary : .secondary)
            Spacer()
        }
    }

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
