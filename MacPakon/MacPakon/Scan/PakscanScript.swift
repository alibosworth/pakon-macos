import Foundation

// One operation from a .pakscan file.
enum PakscanOp {
    case command([UInt8])                                       // O line: raw wire bytes
    case imageRead(Int)                                         // M line: byte count
    case control(UInt8, UInt8, UInt16, UInt16, UInt16, [UInt8]) // C line: brt bReq wVal wIdx wLen data
}

struct PakscanScript {
    let ops: [PakscanOp]

    // Index of the last imageRead op, or nil.
    var lastImageReadIndex: Int? {
        ops.indices.reversed().first { ops[$0].isImageRead }
    }

    // Ops after the last image read (the teardown tail).
    var teardownOps: [PakscanOp] {
        guard let last = lastImageReadIndex else { return [] }
        return Array(ops[(last + 1)...].filter { !$0.isImageRead })
    }

    static func load(url: URL) throws -> PakscanScript {
        let text = try String(contentsOf: url, encoding: .utf8)
        var ops: [PakscanOp] = []

        for rawLine in text.components(separatedBy: .newlines) {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") { continue }

            switch line.first {
            case "O":
                guard let bytes = hexBytes(String(line.dropFirst())) else { continue }
                ops.append(.command(bytes))
            case "M":
                let s = line.dropFirst().trimmingCharacters(in: .whitespaces)
                let count = s.hasPrefix("0x") || s.hasPrefix("0X")
                    ? Int(s.dropFirst(2), radix: 16)
                    : Int(s)
                ops.append(.imageRead(count ?? 20480))
            case "C":
                if let op = parseControlLine(String(line.dropFirst())) {
                    ops.append(op)
                }
            default:
                break
            }
        }
        return PakscanScript(ops: ops)
    }
}

extension PakscanOp {
    var isImageRead: Bool {
        if case .imageRead = self { return true }
        return false
    }
}

// ---- Hex helpers ----

func hexBytes(_ s: String) -> [UInt8]? {
    let filtered = s.filter { !$0.isWhitespace }
    guard filtered.count % 2 == 0 else { return nil }
    var result: [UInt8] = []
    result.reserveCapacity(filtered.count / 2)
    var i = filtered.startIndex
    while i < filtered.endIndex {
        let j = filtered.index(after: i)
        guard let byte = UInt8(filtered[i...j], radix: 16) else { return nil }
        result.append(byte)
        i = filtered.index(after: j)
    }
    return result
}

private func parseControlLine(_ s: String) -> PakscanOp? {
    // Format: "brt bReq wVal wIdx wLen [data hex]"
    let parts = s.split(separator: " ", maxSplits: 5, omittingEmptySubsequences: true)
    guard parts.count >= 5 else { return nil }

    func u8(_ p: Substring) -> UInt8? {
        let t = p.hasPrefix("0x") || p.hasPrefix("0X") ? String(p.dropFirst(2)) : String(p)
        return UInt8(t, radix: 16)
    }
    func u16(_ p: Substring) -> UInt16? {
        let t = p.hasPrefix("0x") || p.hasPrefix("0X") ? String(p.dropFirst(2)) : String(p)
        return UInt16(t, radix: 16)
    }

    guard let brt  = u8(parts[0]),
          let breq = u8(parts[1]),
          let wval = u16(parts[2]),
          let widx = u16(parts[3]),
          let wlen = u16(parts[4]) else { return nil }

    let dataStr = parts.count > 5 ? String(parts[5]) : ""
    let data = hexBytes(dataStr) ?? []
    return .control(brt, breq, wval, widx, wlen, data)
}
