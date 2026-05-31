#!/usr/bin/env python3
"""
analyze_capture.py — Phase 4 USB capture analyzer for the Pakon backend.

Ingests a capture of the working driver talking to the scanner and emits an
annotated, time-ordered URB timeline: it decodes control-transfer setup packets
(the vendor request codes we need), flags 36-byte payloads and decodes the
Pakon command frame, and prints a summary of the distinct transfer/endpoint/
request combinations seen. That summary is what reveals whether commands ride
EP0 control transfers (expected) or a bulk channel, and what the init/open
sequence is.

Inputs (auto-detected by extension, override with --format):
  - .pcapng / .pcap : parsed via `tshark` (must be on PATH)
  - tshark TSV       : `--format tsv`   (the field set this script requests)
  - usbmon text      : `--format usbmon` (from /sys/.../usbmon/<bus>u; note that
                       usbmon text truncates data per URB, ~32 bytes)

This tool only parses USB; it encodes no guesses about the Pakon protocol
beyond the documented 36-byte frame layout.
"""
import argparse
import signal
import struct
import subprocess
import sys
from collections import Counter

# Behave like a normal Unix filter when piped into `head`/`less`: die quietly
# on SIGPIPE instead of raising BrokenPipeError. (POSIX only.)
try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):
    pass

# Documented frame enums (kept in sync with include/pakon_proto.h).
ADDR = {0x10: "AD_HOST", 0x20: "AD_PICL", 0x22: "AD_BOOT_PICL",
        0x24: "AD_PICM", 0x26: "AD_BOOT_PICM", 0x40: "AD_PICL_PLUS",
        0x42: "AD_BOOT_PICL_PLUS", 0x44: "AD_PICM_PLUS",
        0x46: "AD_BOOT_PICM_PLUS"}
STATUS = {0: "success", 1: "not_acked", 2: "invalid_pkt", 3: "bad_checksum",
          4: "usb4", 5: "usb5", 6: "usb6", 7: "host_algo", 8: "success8",
          9: "bus_error"}

# Linux pcap URB transfer_type -> name.
TT = {0: "ISO", 1: "INTR", 2: "CTRL", 3: "BULK"}

# tshark fields requested for the TSV/pcapng path (order matters).
TSHARK_FIELDS = [
    "frame.number", "frame.time_relative",
    "usb.bus_id", "usb.device_address", "usb.endpoint_address",
    "usb.transfer_type", "usb.urb_type",
    "usb.bmRequestType", "usb.setup.bRequest",
    "usb.setup.wValue", "usb.setup.wIndex", "usb.setup.wLength",
    "usb.capdata",
]


def _hexbytes(s):
    """Parse 'aa:bb:cc' or 'aabbcc' or 'aa bb cc' into a bytes object."""
    if not s:
        return b""
    s = s.replace(":", "").replace(" ", "")
    try:
        return bytes.fromhex(s)
    except ValueError:
        return b""


def _intish(s):
    if s is None or s == "":
        return None
    try:
        return int(s, 0)
    except ValueError:
        try:
            return int(s)
        except ValueError:
            return None


class Rec:
    __slots__ = ("ts", "ttype", "direction", "bus", "dev", "ep", "urb",
                 "setup", "data", "urbid")

    def __init__(self):
        self.ts = 0.0
        self.ttype = "?"
        self.direction = "?"
        self.bus = None
        self.dev = None
        self.ep = None
        self.urb = "?"
        self.setup = None     # dict for control submits
        self.data = b""
        self.urbid = None     # pairs a submit (S) with its completion (C)


def _is_vendor(setup):
    return bool(setup) and ((setup.get("bmRequestType", 0) >> 5) & 3) == 2


def parse_tshark_tsv(lines):
    recs = []
    for line in lines:
        f = line.rstrip("\n").split("\t")
        if len(f) < len(TSHARK_FIELDS):
            f += [""] * (len(TSHARK_FIELDS) - len(f))
        (num, trel, bus, dev, epaddr, tt, urb,
         brt, breq, wval, widx, wlen, capdata) = f[:len(TSHARK_FIELDS)]
        r = Rec()
        r.ts = float(trel) if trel else 0.0
        r.bus = _intish(bus)
        r.dev = _intish(dev)
        ep = _intish(epaddr)
        r.ep = ep
        r.direction = "IN" if (ep is not None and ep & 0x80) else "OUT"
        ttv = _intish(tt)
        r.ttype = TT.get(ttv, "?") if ttv is not None else "?"
        u = urb.strip().strip("'\"")
        r.urb = {"S": "S", "C": "C", "0x53": "S", "0x43": "C",
                 "83": "S", "67": "C"}.get(u, u[:1] or "?")
        if _intish(brt) is not None:
            r.setup = {
                "bmRequestType": _intish(brt), "bRequest": _intish(breq),
                "wValue": _intish(wval), "wIndex": _intish(widx),
                "wLength": _intish(wlen),
            }
        r.data = _hexbytes(capdata)
        recs.append(r)
    return recs


def parse_usbmon_text(lines):
    """Parse the kernel usbmon 'u' text format."""
    recs = []
    for line in lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        # parts: tag, ts_us, type(S/C/E), "Co:1:008:0", ...rest
        try:
            ts_us = int(parts[1])
        except ValueError:
            continue
        urb = parts[2]
        addr = parts[3]
        if ":" not in addr:
            continue
        xfer = addr[0]            # C/Z/I/B
        dirc = addr[1] if len(addr) > 1 else "?"
        bits = addr.split(":")
        bus = _intish(bits[1]) if len(bits) > 1 else None
        dev = _intish(bits[2]) if len(bits) > 2 else None
        ep = _intish(bits[3]) if len(bits) > 3 else None
        r = Rec()
        r.ts = ts_us / 1e6
        r.bus, r.dev = bus, dev
        r.ep = ep
        r.ttype = {"C": "CTRL", "Z": "ISO", "I": "INTR", "B": "BULK"}.get(
            xfer, "?")
        r.direction = "IN" if dirc == "i" else "OUT"
        # usbmon text gives the endpoint number without the direction bit;
        # OR it back in so r.ep matches bEndpointAddress (e.g. 0x86) as the
        # descriptors and the tshark path report it.
        if r.direction == "IN" and r.ep is not None:
            r.ep |= 0x80
        r.urb = urb if urb in ("S", "C", "E") else "?"

        rest = parts[4:]
        # Control submit carries a setup packet: 's' bmReq bReq wValue wIndex wLen
        if rest and rest[0] == "s" and len(rest) >= 6:
            r.setup = {
                "bmRequestType": _intish("0x" + rest[1]),
                "bRequest": _intish("0x" + rest[2]),
                "wValue": _intish("0x" + rest[3]),
                "wIndex": _intish("0x" + rest[4]),
                "wLength": _intish("0x" + rest[5]),
            }
            rest = rest[6:]
        # Data follows a '=' marker, as 4-byte hex words.
        if "=" in rest:
            data_words = rest[rest.index("=") + 1:]
            r.data = _hexbytes("".join(data_words))
        recs.append(r)
    return recs


# Linux usbmon pcap link types and header sizes.
LINKTYPE_USB_LINUX = 189          # 48-byte header
LINKTYPE_USB_LINUX_MMAPPED = 220  # 64-byte header


def _usbmon_record(data, hdrlen, end):
    """Build a Rec from one usbmon packet (header + payload)."""
    if len(data) < 48:
        return None
    r = Rec()
    r.urbid = struct.unpack(end + "Q", data[0:8])[0]
    r.urb = {0x53: "S", 0x43: "C", 0x45: "E"}.get(data[8], "?")
    xfer = data[9]
    r.ttype = TT.get(xfer, "?")
    epnum = data[10]
    r.ep = epnum
    r.direction = "IN" if (epnum & 0x80) else "OUT"
    r.dev = data[11]
    r.bus = struct.unpack(end + "H", data[12:14])[0]
    flag_setup = data[14]
    ts_sec = struct.unpack(end + "q", data[16:24])[0]
    ts_usec = struct.unpack(end + "i", data[24:28])[0]
    r.ts = ts_sec + ts_usec / 1e6
    len_cap = struct.unpack(end + "I", data[36:40])[0]
    # setup packet (8 bytes at offset 40) is valid when flag_setup == 0; USB
    # setup fields are little-endian on the wire.
    if xfer == 2 and flag_setup == 0:
        s = data[40:48]
        r.setup = {
            "bmRequestType": s[0], "bRequest": s[1],
            "wValue": s[2] | (s[3] << 8), "wIndex": s[4] | (s[5] << 8),
            "wLength": s[6] | (s[7] << 8),
        }
    r.data = bytes(data[hdrlen:hdrlen + len_cap])
    return r


def parse_pcapng(path):
    """Pure-Python pcapng reader for Linux usbmon captures (no tshark)."""
    with open(path, "rb") as fh:
        blob = memoryview(fh.read())
    if len(blob) < 12 or bytes(blob[0:4]) != b"\x0a\x0d\x0d\x0a":
        sys.exit("not a pcapng file (or unsupported); use --format tsv/usbmon")
    # Byte order from the Section Header Block's magic at offset 8.
    end = "<" if struct.unpack("<I", blob[8:12])[0] == 0x1A2B3C4D else ">"

    recs = []
    iface_linktype = []
    off = 0
    n = len(blob)
    while off + 12 <= n:
        btype = struct.unpack(end + "I", blob[off:off + 4])[0]
        blen = struct.unpack(end + "I", blob[off + 4:off + 8])[0]
        if blen < 12 or off + blen > n:
            break
        body = blob[off + 8:off + blen - 4]
        if btype == 0x00000001:                       # Interface Description
            lt = struct.unpack(end + "H", body[0:2])[0]
            iface_linktype.append(lt)
        elif btype == 0x00000006:                      # Enhanced Packet Block
            iface_id = struct.unpack(end + "I", body[0:4])[0]
            cap_len = struct.unpack(end + "I", body[12:16])[0]
            pkt = body[20:20 + cap_len]
            lt = iface_linktype[iface_id] if iface_id < len(iface_linktype) \
                else LINKTYPE_USB_LINUX_MMAPPED
            hdrlen = 48 if lt == LINKTYPE_USB_LINUX else 64
            rec = _usbmon_record(pkt, hdrlen, end)
            if rec:
                recs.append(rec)
        elif btype == 0x00000003:                      # Simple Packet Block
            pass  # no per-interface id / timestamp; skipped (dumpcap uses EPB)
        off += blen
    return recs


def run_tshark(path):
    cmd = ["tshark", "-r", path, "-T", "fields", "-E", "separator=\t",
           "-E", "occurrence=f"]
    for fld in TSHARK_FIELDS:
        cmd += ["-e", fld]
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit("error: tshark not found on PATH (install wireshark/tshark)")
    except subprocess.CalledProcessError as e:
        sys.exit(f"tshark failed: {e.stderr}")
    return out.stdout.splitlines()


def decode_setup(s):
    brt = s["bmRequestType"] or 0
    d = "IN" if brt & 0x80 else "OUT"
    typ = {0: "std", 1: "class", 2: "vendor", 3: "reserved"}[(brt >> 5) & 3]
    rcp = {0: "device", 1: "interface", 2: "endpoint"}.get(brt & 0x1f, "other")
    return (f"SETUP {d} {typ}/{rcp} bRequest=0x{(s['bRequest'] or 0):02x} "
            f"wValue=0x{(s['wValue'] or 0):04x} wIndex=0x{(s['wIndex'] or 0):04x} "
            f"wLength={s['wLength']}")


def decode_pakon_frame(data):
    """Decode a Pakon command/response frame, or None if it isn't one.

    Observed wire framing: [type][count][count data bytes], so a real frame is
    exactly 2 + count bytes. That self-consistency check cleanly distinguishes
    EP1 command frames from image data and the 0xA9 control reads.
    """
    if len(data) < 2:
        return None
    typ, count = data[0], data[1]
    if len(data) != 2 + count:
        return None
    parts = [f"type=0x{typ:02x}", f"count={count}"]
    if count >= 1:
        a = data[2]
        parts.append(f"addr={ADDR.get(a, f'0x{a:02x}')}")
    if count >= 2:
        st = data[3]   # reply status byte (data[1] of payload)
        parts.append(f"st={STATUS.get(st, f'0x{st:02x}')}")
    return "PAKON " + " ".join(parts)


# FX2 firmware-download vendor requests.
FW_REQUESTS = {0xA0, 0xA3, 0xA4, 0xA9}


def extract_firmware(recs, path, fw_device=None):
    """Write the firmware-download control transfers to a replayable .pakfw
    script: one line per transfer, `bmRequestType bRequest wValue wIndex
    wLength [dataHex]` (all hex). Replayed verbatim by pakon_usb_load_firmware
    to drive a cold f235 device to operational f135 — no .hex blob needed."""
    subs = [r for r in recs if r.urb == "S" and r.setup
            and r.setup.get("bRequest") in FW_REQUESTS]
    if fw_device is None:
        # bootstrap device = the one carrying the most 0xA0/0xA3 writes
        cnt = Counter(r.dev for r in subs
                      if r.setup["bRequest"] in (0xA0, 0xA3))
        if not cnt:
            sys.exit("no FX2 firmware transfers (0xA0/0xA3) found in capture")
        fw_device = cnt.most_common(1)[0][0]
    seq = [r for r in subs if r.dev == fw_device]
    if not seq:
        sys.exit(f"no firmware transfers on device {fw_device}")

    with open(path, "w") as fh:
        fh.write("# pakon firmware control-transfer script (from capture)\n")
        fh.write(f"# device {fw_device}, {len(seq)} transfers\n")
        fh.write("# bmRequestType bRequest wValue wIndex wLength [dataHex]\n")
        for r in seq:
            s = r.setup
            line = (f"{s['bmRequestType']:02x} {s['bRequest']:02x} "
                    f"{s['wValue']:04x} {s['wIndex']:04x} {s['wLength']:04x}")
            if r.data:
                line += " " + bytes(r.data).hex()
            fh.write(line + "\n")
    nbytes = sum(len(r.data) for r in seq)
    reqs = Counter(r.setup["bRequest"] for r in seq)
    print(f"wrote {path}: {len(seq)} transfers from device {fw_device}, "
          f"{nbytes} firmware bytes")
    print("  requests: " + ", ".join(f"0x{k:02x}×{v}"
                                      for k, v in sorted(reqs.items())))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("capture")
    ap.add_argument("--format",
                    choices=["auto", "pcapng", "tshark", "tsv", "usbmon"],
                    default="auto")
    ap.add_argument("--bus", type=int, help="filter to this USB bus")
    ap.add_argument("--device", type=int, help="filter to this device number")
    ap.add_argument("--max-data", type=int, default=40,
                    help="max payload bytes to print per URB (default 40)")
    ap.add_argument("--commands", action="store_true",
                    help="hide standard/class USB chatter; show only vendor "
                         "control transfers + bulk/interrupt (the protocol)")
    ap.add_argument("--extract-firmware", metavar="OUT.pakfw",
                    help="extract the FX2 firmware-download control transfers "
                         "(0xA0/0xA3/0xA4/0xA9) to a replayable script and exit")
    ap.add_argument("--fw-device", type=int,
                    help="device number of the firmware-load (bootstrap) device "
                         "for --extract-firmware (auto-detected if omitted)")
    args = ap.parse_args()

    fmt = args.format
    if fmt == "auto":
        if args.capture.endswith((".pcapng", ".pcap")):
            fmt = "pcapng"
        elif args.capture.endswith((".tsv", ".txt")):
            fmt = "usbmon" if args.capture.endswith(".txt") else "tsv"
        else:
            fmt = "pcapng"

    if fmt == "pcapng":
        recs = parse_pcapng(args.capture)            # native, no tshark
    elif fmt == "tshark":
        recs = parse_tshark_tsv(run_tshark(args.capture))
    else:
        with open(args.capture, "r", errors="replace") as fh:
            lines = fh.readlines()
        recs = parse_tshark_tsv(lines) if fmt == "tsv" else parse_usbmon_text(lines)

    if args.extract_firmware:
        extract_firmware(recs, args.extract_firmware, args.fw_device)
        return

    # Inventory of (bus, device) before filtering — helps pick --device, since
    # the device number changes on every re-enumeration (e.g. across an
    # f235->f135 firmware reload).
    inv = Counter((r.bus, r.dev) for r in recs)
    print("# bus/device inventory (URB counts):")
    for (bus, dev), cnt in sorted(inv.items(),
                                  key=lambda kv: (-kv[1], kv[0])):
        print(f"#   bus {bus} device {dev}: {cnt}")
    print()

    if args.bus is not None:
        recs = [r for r in recs if r.bus == args.bus]
    if args.device is not None:
        recs = [r for r in recs if r.dev == args.device]

    if not recs:
        sys.exit("no matching URBs (check --bus/--device against the inventory)")

    print(f"# {len(recs)} URBs"
          + (f" (bus {args.bus})" if args.bus is not None else "")
          + (f" (device {args.device})" if args.device is not None else ""))
    print("# time      type dir ep    detail")

    t0 = min((r.ts for r in recs), default=0.0)
    pending = {}     # urbid -> originating setup (so completions know it)
    combos = Counter()
    for r in recs:
        r.ts -= t0
        # Pair a completion with the setup from its submit.
        if r.urb == "S" and r.setup is not None and r.urbid is not None:
            pending[r.urbid] = r.setup
        eff_setup = r.setup or (pending.get(r.urbid)
                                if r.urbid is not None else None)

        # --commands: drop standard/class control chatter, keep vendor + data.
        if args.commands and r.ttype == "CTRL" and not _is_vendor(eff_setup):
            continue

        epname = f"0x{r.ep:02x}" if r.ep is not None else "?"
        detail = ""
        if r.setup:
            detail = decode_setup(r.setup)
            combos[(r.ttype, "ctrl", r.setup.get("bRequest"))] += 1
        elif r.urb != "C":
            combos[(r.ttype, r.direction, r.ep)] += 1
        if r.data:
            # decode_pakon_frame() self-validates (len == 2 + count), so it
            # only annotates real command frames, not image data / 0xA9 reads.
            frame = decode_pakon_frame(r.data)
            shown = r.data[:args.max_data].hex(" ")
            more = "…" if len(r.data) > args.max_data else ""
            detail += (("  " if detail else "")
                       + (f"[{frame}] " if frame else "")
                       + f"data({len(r.data)}): {shown}{more}")
        dev = f"d{r.dev}" if r.dev is not None else "d?"
        print(f"{r.ts:9.4f} {dev:4} {r.ttype:4} {r.direction:3} {epname:5} "
              f"{r.urb} {detail}")

    print("\n# distinct transfer/endpoint/request combinations:")
    for key, cnt in combos.most_common():
        ttype, k2, k3 = key
        if k2 == "ctrl":
            req = f"bRequest=0x{k3:02x}" if k3 is not None else "bRequest=?"
            print(f"  {ttype:4} control {req}: {cnt}")
        else:
            ep = f"0x{k3:02x}" if k3 is not None else "?"
            print(f"  {ttype:4} {k2:3} ep {ep}: {cnt}")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # downstream (head/less) closed the pipe; exit quietly
        try:
            sys.stdout.close()
        except Exception:
            pass
