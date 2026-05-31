import Foundation
import Combine

@MainActor
final class ScannerModel: ObservableObject {

    enum ConnectionState: Equatable {
        case disconnected
        case cold
        case warm
    }

    enum ScanState: Equatable {
        case idle
        case running
        case done(url: URL)
        case failed(String)
    }

    enum FirmwareState: Equatable {
        case idle
        case loading(progress: Double)
        case failed(String)
    }

    @Published var connectionState: ConnectionState = .disconnected
    @Published var scanState: ScanState = .idle
    @Published var firmwareState: FirmwareState = .idle
    @Published var bytesReceived: Int64 = 0

    var isConnected: Bool { connectionState == .warm }
    var isCold: Bool { connectionState == .cold }
    var canScan: Bool { isConnected && scanState == .idle && firmwareState == .idle }

    private var scanTask: Task<Void, Never>?
    private var pollingTimer: Timer?

    // ---- Connection polling ----

    func startPolling() {
        pollingTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                if PakonUSBDevice.isScannerConnected() {
                    self.connectionState = .warm
                } else if PakonUSBDevice.isColdScannerConnected() {
                    self.connectionState = .cold
                } else {
                    self.connectionState = .disconnected
                }
            }
        }
    }

    func stopPolling() {
        pollingTimer?.invalidate()
        pollingTimer = nil
    }

    // ---- Firmware load ----

    func loadFirmware(scriptURL: URL? = nil) {
        guard isCold, firmwareState == .idle else { return }
        let scriptURL = scriptURL
            ?? Bundle.main.url(forResource: "f135", withExtension: "pakfw")
        guard let scriptURL else { return }
        firmwareState = .loading(progress: 0)

        Task.detached(priority: .userInitiated) { [weak self] in
            let session = PakonFirmwareSession()
            do {
                try session.load(scriptURL: scriptURL) { done, total in
                    let pct = total > 0 ? Double(done) / Double(total) : 0
                    Task { @MainActor [weak self] in
                        self?.firmwareState = .loading(progress: pct)
                    }
                }
                await MainActor.run { [weak self] in
                    self?.firmwareState = .idle
                    // Polling will pick up the warm device on next tick.
                }
            } catch {
                await MainActor.run { [weak self] in
                    self?.firmwareState = .failed(error.localizedDescription)
                }
            }
        }
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
                    self?.scanState = Task.isCancelled ? .idle : .failed(error.localizedDescription)
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
        String(format: "%.1f MB", Double(bytesReceived) / 1_048_576.0)
    }
}
