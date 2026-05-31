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
import subprocess
import sys
from collections import Counter

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
    "usb.setup.bmRequestType", "usb.setup.bRequest",
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
                 "setup", "data")

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
    """Decode a 36-byte (or close) Pakon command/response frame header."""
    if len(data) < 4:
        return None
    typ, count, b2, b3 = data[0], data[1], data[2], data[3]
    addr = ADDR.get(b2, f"0x{b2:02x}")
    note = f"PAKON type=0x{typ:02x} count={count} addr={addr}"
    # In a scanner->host reply the status is the byte after the address.
    if b3 in STATUS:
        note += f" status?={STATUS[b3]}"
    return note


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("capture")
    ap.add_argument("--format", choices=["auto", "pcapng", "tsv", "usbmon"],
                    default="auto")
    ap.add_argument("--bus", type=int, help="filter to this USB bus")
    ap.add_argument("--device", type=int, help="filter to this device number")
    ap.add_argument("--max-data", type=int, default=40,
                    help="max payload bytes to print per URB (default 40)")
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
        recs = parse_tshark_tsv(run_tshark(args.capture))
    else:
        with open(args.capture, "r", errors="replace") as fh:
            lines = fh.readlines()
        recs = parse_tshark_tsv(lines) if fmt == "tsv" else parse_usbmon_text(lines)

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

    combos = Counter()
    for r in recs:
        epname = f"0x{r.ep:02x}" if r.ep is not None else "?"
        detail = ""
        if r.setup:
            detail = decode_setup(r.setup)
            combos[(r.ttype, "ctrl", r.setup.get("bRequest"))] += 1
        else:
            combos[(r.ttype, r.direction, r.ep)] += 1
        if r.data:
            # Only decode a Pakon frame on the command channel (control
            # transfers) or an exact 36-byte payload — not bulk image data.
            cmdish = (r.ttype == "CTRL") or (len(r.data) == 36)
            frame = decode_pakon_frame(r.data) if (cmdish and len(r.data) >= 4) \
                else None
            shown = r.data[:args.max_data].hex(" ")
            more = "…" if len(r.data) > args.max_data else ""
            detail += (("  " if detail else "")
                       + (f"[{frame}] " if frame and len(r.data) >= 4 else "")
                       + f"data({len(r.data)}): {shown}{more}")
        print(f"{r.ts:9.4f} {r.ttype:4} {r.direction:3} {epname:5} "
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
    main()
