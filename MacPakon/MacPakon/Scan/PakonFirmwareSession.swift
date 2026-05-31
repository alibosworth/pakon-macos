import Foundation

/// Parses a .pakfw script and replays it over EP0 to bring a cold scanner
/// (0F05:F235) to operational state (0F05:F135).
final class PakonFirmwareSession {

    enum FirmwareError: LocalizedError {
        case scriptLoad(Error)
        case deviceOpen(Error)
        case warmDeviceTimeout

        var errorDescription: String? {
            switch self {
            case .scriptLoad(let e):  return "Script: \(e.localizedDescription)"
            case .deviceOpen(let e):  return "USB: \(e.localizedDescription)"
            case .warmDeviceTimeout:  return "Scanner did not re-enumerate after firmware load"
            }
        }
    }

    /// `progress` fires with (completedTransfers, totalTransfers).
    func load(scriptURL: URL,
              timeout: TimeInterval = 2.0,
              progress: @escaping (Int, Int) -> Void) throws {

        let transfers = try parseScript(url: scriptURL)
        let total = transfers.count

        let usb: PakonUSBDevice
        do { usb = try PakonUSBDevice.openCold() }
        catch { throw FirmwareError.deviceOpen(error) }

        // Tolerate all per-transfer errors — the device re-enumerates part-way
        // through the sequence, making the tail transfers fail. This matches the
        // C implementation which counts errors but never aborts.
        for (i, xfer) in transfers.enumerated() {
            try? usb.sendControlTransferType(xfer.bmRequestType,
                                             request: xfer.bRequest,
                                             value: xfer.wValue,
                                             index: xfer.wIndex,
                                             length: xfer.wLength,
                                             data: xfer.data,
                                             timeout: timeout)
            progress(i + 1, total)
        }

        // Device has re-enumerated; the old handle is dead — just drop it.
        usb.close()

        // Wait up to 10 s for the warm device to appear.
        let deadline = Date().addingTimeInterval(10)
        while Date() < deadline {
            if PakonUSBDevice.isScannerConnected() { return }
            usleep(200_000)
        }
        throw FirmwareError.warmDeviceTimeout
    }

    // ---- Parser ----

    private struct Transfer {
        let bmRequestType: UInt8
        let bRequest: UInt8
        let wValue: UInt16
        let wIndex: UInt16
        let wLength: UInt16  // authoritative length from script
        let data: Data?      // nil for IN transfers or zero-length OUT
    }

    private func parseScript(url: URL) throws -> [Transfer] {
        let text: String
        do { text = try String(contentsOf: url, encoding: .utf8) }
        catch { throw FirmwareError.scriptLoad(error) }

        var result: [Transfer] = []
        for raw in text.components(separatedBy: .newlines) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") { continue }

            let parts = line.split(separator: " ", omittingEmptySubsequences: true)
            guard parts.count >= 5,
                  let brt  = UInt8(parts[0],  radix: 16),
                  let breq = UInt8(parts[1],  radix: 16),
                  let wval = UInt16(parts[2], radix: 16),
                  let widx = UInt16(parts[3], radix: 16),
                  let wlen = UInt16(parts[4], radix: 16)
            else { continue }

            let dataHex = parts.count > 5 ? String(parts[5]) : ""
            let payload: Data? = dataHex.isEmpty ? nil : hexData(dataHex)

            result.append(Transfer(bmRequestType: brt, bRequest: breq,
                                   wValue: wval, wIndex: widx,
                                   wLength: wlen, data: payload))
        }
        return result
    }
}

private func hexData(_ s: String) -> Data? {
    guard s.count % 2 == 0 else { return nil }
    var bytes: [UInt8] = []
    bytes.reserveCapacity(s.count / 2)
    var i = s.startIndex
    while i < s.endIndex {
        let j = s.index(after: i)
        guard let b = UInt8(s[i...j], radix: 16) else { return nil }
        bytes.append(b)
        i = s.index(after: j)
    }
    return Data(bytes)
}
