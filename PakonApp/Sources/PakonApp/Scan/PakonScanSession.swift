import Foundation
import PakonUSBObjC

// Autostop thresholds (mirrors pakon_replay.c constants).
private let whiteThreshold: UInt16 = 40_000
private let whiteFracPct = 90
private let trailWhiteNeeded = 8

/// Runs a verbatim .pakscan replay with end-of-roll autostop.
/// Call `run(...)` on a background thread; progress fires on that same thread.
final class PakonScanSession {

    enum SessionError: LocalizedError {
        case usbOpen(Error)
        case scriptLoad(Error)
        case cancelled

        var errorDescription: String? {
            switch self {
            case .usbOpen(let e):    return "USB: \(e.localizedDescription)"
            case .scriptLoad(let e): return "Script: \(e.localizedDescription)"
            case .cancelled:         return "Cancelled"
            }
        }
    }

    func run(scriptURL: URL,
             outputURL: URL,
             timeout: TimeInterval = 2.0,
             imagePipeTimeout: TimeInterval = 5.0,
             progress: @escaping (Int64) -> Void,
             isCancelled: @escaping () -> Bool) throws {

        // Load script.
        let script: PakscanScript
        do { script = try PakscanScript.load(url: scriptURL) }
        catch { throw SessionError.scriptLoad(error) }

        // Open USB device. Swift bridging turns openWithError: into throws.
        let usb: PakonUSBDevice
        do { usb = try PakonUSBDevice.open() }
        catch { throw SessionError.usbOpen(error) }
        defer { usb.close() }

        // Open output file.
        FileManager.default.createFile(atPath: outputURL.path, contents: nil)
        guard let outFile = FileHandle(forWritingAtPath: outputURL.path) else {
            throw CocoaError(.fileWriteNoPermission)
        }
        defer { try? outFile.close() }

        var imgBytesTotal: Int64 = 0
        var filmSeen = false
        var trailWhiteCount = 0
        var stoppedEarly = false

        // Open handshake.
        try pakonOpenHandshake(usb: usb)

        // Replay script ops.
        opLoop: for op in script.ops {
            if isCancelled() { throw SessionError.cancelled }

            switch op {
            case .command(let bytes):
                try? usb.sendCommand(Data(bytes), timeout: timeout)
                _ = try? usb.receiveCommandMaxLength(64, timeout: timeout)

            case .imageRead(let wantBytes):
                let chunk = usb.receiveImageMaxLength(UInt(wantBytes), timeout: imagePipeTimeout)
                if !chunk.isEmpty {
                    outFile.write(chunk)
                    imgBytesTotal += Int64(chunk.count)
                    progress(imgBytesTotal)

                    if isWhiteChunk(chunk) {
                        if filmSeen {
                            trailWhiteCount += 1
                            if trailWhiteCount >= trailWhiteNeeded {
                                stoppedEarly = true
                                break opLoop
                            }
                        }
                    } else {
                        filmSeen = true
                        trailWhiteCount = 0
                    }
                }

            case .control(let brt, let breq, let wval, let widx, let wlen, let data):
                _ = try? usb.controlTransferType(brt, request: breq,
                                                  value: wval, index: widx,
                                                  length: wlen,
                                                  send: data.isEmpty ? nil : Data(data),
                                                  timeout: timeout)
            }
        }

        // If we exited mid-script, replay the teardown tail to leave the device in a clean state.
        if stoppedEarly {
            for op in script.teardownOps {
                if isCancelled() { break }
                switch op {
                case .command(let bytes):
                    try? usb.sendCommand(Data(bytes), timeout: timeout)
                    _ = try? usb.receiveCommandMaxLength(64, timeout: timeout)
                case .control(let brt, let breq, let wval, let widx, let wlen, let data):
                    _ = try? usb.controlTransferType(brt, request: breq,
                                                      value: wval, index: widx,
                                                      length: wlen,
                                                      send: data.isEmpty ? nil : Data(data),
                                                      timeout: timeout)
                case .imageRead:
                    break
                }
            }
        }
    }

    // ---- End-of-roll white detection ----

    private func isWhiteChunk(_ data: Data) -> Bool {
        let samples = data.count / 2
        guard samples > 0 else { return false }
        var whiteCount = 0
        data.withUnsafeBytes { ptr in
            guard let base = ptr.baseAddress else { return }
            let u16s = base.assumingMemoryBound(to: UInt16.self)
            for i in 0 ..< samples {
                if UInt16(littleEndian: u16s[i]) > whiteThreshold { whiteCount += 1 }
            }
        }
        return whiteCount * 100 >= samples * whiteFracPct
    }
}
