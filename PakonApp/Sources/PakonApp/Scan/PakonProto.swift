import Foundation
import PakonUSBObjC

enum PakonError: LocalizedError {
    case shortReply(Int)
    case usbError(Error)
    case noReply
    case deviceError(UInt8)

    var errorDescription: String? {
        switch self {
        case .shortReply(let n):   return "Short reply: \(n) bytes"
        case .usbError(let e):     return "USB error: \(e.localizedDescription)"
        case .noReply:             return "No reply from scanner"
        case .deviceError(let st): return "Scanner error status: 0x\(String(st, radix: 16))"
        }
    }
}

// Wire frame: [type][count][data…] where count = len(data).
struct PakonFrame {
    let type: UInt8
    let data: [UInt8]  // data[0] = address byte

    var wireBytes: Data {
        Data([type, UInt8(data.count)] + data)
    }

    var status: UInt8 { data.count >= 2 ? data[1] : 0xFF }
    var address: UInt8 { data.first ?? 0 }

    static func parse(_ raw: Data) throws -> PakonFrame {
        guard raw.count >= 2 else { throw PakonError.shortReply(raw.count) }
        let count = Int(raw[1])
        guard raw.count >= 2 + count else { throw PakonError.shortReply(raw.count) }
        return PakonFrame(type: raw[0], data: Array(raw[2 ..< 2 + count]))
    }
}

// ---- Command exchange ----
// ObjC sendCommand/receiveCommandMaxLength bridge to throws in Swift (BOOL+error: convention).

func pakonExchange(usb: PakonUSBDevice, frame: PakonFrame,
                   timeout: TimeInterval = 2.0) throws -> PakonFrame {
    try usb.sendCommand(frame.wireBytes, timeout: timeout)

    let reply = try usb.receiveCommandMaxLength(64, timeout: timeout)
    guard !reply.isEmpty else { throw PakonError.noReply }
    return try PakonFrame.parse(reply)
}

// ---- Open handshake (confirmed from capture) ----

func pakonOpenHandshake(usb: PakonUSBDevice) throws {
    // Each step: raw wire bytes to send, expected address byte in reply.
    let steps: [(wire: [UInt8], expectAddr: UInt8)] = [
        ([0x04, 0x03, 0x10, 0x00, 0x85], 0x10),
        ([0x02, 0x04, 0x10, 0x01, 0x8f, 0x00], 0x10),
        ([0x04, 0x03, 0x44, 0x00, 0x00], 0x44),
        ([0x04, 0x03, 0x46, 0x00, 0x00], 0x46),
        ([0x04, 0x03, 0x24, 0x00, 0x00], 0x24),
    ]

    for step in steps {
        let wireData = Data(step.wire)
        try usb.sendCommand(wireData, timeout: 2.0)
        let replyData = try usb.receiveCommandMaxLength(64, timeout: 2.0)
        let reply = try PakonFrame.parse(replyData)
        guard reply.address == step.expectAddr else {
            throw PakonError.deviceError(reply.status)
        }
    }
}
