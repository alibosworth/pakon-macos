import Foundation
import Combine
import PakonUSBObjC

@MainActor
final class ScannerModel: ObservableObject {

    enum ScanState: Equatable {
        case idle
        case running
        case done(url: URL)
        case failed(String)
    }

    @Published var isConnected = false
    @Published var scanState: ScanState = .idle
    @Published var bytesReceived: Int64 = 0

    var canScan: Bool { isConnected && scanState == .idle }

    private var scanTask: Task<Void, Never>?
    private var pollingTimer: Timer?

    // ---- Connection polling ----

    func startPolling() {
        pollingTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.isConnected = PakonUSBDevice.isScannerConnected()
            }
        }
    }

    func stopPolling() {
        pollingTimer?.invalidate()
        pollingTimer = nil
    }

    // ---- Scan ----

    func startScan(scriptURL: URL, outputDir: URL) {
        guard scanState == .idle else { return }
        scanState = .running
        bytesReceived = 0

        scanTask = Task.detached(priority: .userInitiated) { [weak self] in
            let session = PakonScanSession()
            let stamp = ISO8601DateFormatter().string(from: Date())
                .replacingOccurrences(of: ":", with: "-")
            let rawURL = outputDir.appendingPathComponent("scan_\(stamp).raw")

            do {
                try session.run(
                    scriptURL: scriptURL,
                    outputURL: rawURL,
                    progress: { bytes in
                        Task { @MainActor [weak self] in
                            self?.bytesReceived = bytes
                        }
                    },
                    isCancelled: { Task.isCancelled }
                )
                await MainActor.run { [weak self] in
                    self?.scanState = .done(url: rawURL)
                }
            } catch {
                await MainActor.run { [weak self] in
                    if Task.isCancelled {
                        self?.scanState = .idle
                    } else {
                        self?.scanState = .failed(error.localizedDescription)
                    }
                }
            }
        }
    }

    func cancel() {
        scanTask?.cancel()
        scanTask = nil
        scanState = .idle
    }

    // ---- Helpers ----

    var formattedBytes: String {
        let mb = Double(bytesReceived) / 1_048_576.0
        return String(format: "%.1f MB", mb)
    }
}
