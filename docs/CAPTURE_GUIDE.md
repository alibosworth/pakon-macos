# Capture guide (Phase 4)

Goal: record the **working driver** driving the scanner so we can decode the
undocumented init/command/scan path (we proved in Phase 2 that the device NAKs
raw bulk, so the protocol is something we must observe, not guess).

This guide targets the available setup: a **Linux host running a Windows VM
with the scanner passed through**. The win here is that passed-through USB URBs
still traverse the host's USB controller, so we capture on the **Linux host
with usbmon** — nothing extra needs installing inside Windows.

## 0. Prerequisites

- In the VM, confirm the driver actually scans (Kaufman's FX35 driver +
  TLXClientDemo, or the original Pakon/TLX software). We need a *successful*
  scan to capture.
- **Two firmware stages:** the device boots as bootstrap `0f05:f235`, and the
  driver downloads stage-2 firmware so it re-enumerates as operational
  `0f05:f135`. A VirtualBox USB filter matching only `f235` is sufficient —
  VirtualBox keeps the device attached to the VM through the re-enumeration
  (confirmed: full scans work with an `f235`-only filter). A vendor-only filter
  (`Vendor ID = 0f05`, Product ID blank) also works if you prefer.
- On the host: `sudo apt install wireshark tshark` (or distro equivalent).
  `dumpcap`/`tshark` come with Wireshark. Add yourself to the `wireshark` group
  or just run capture with `sudo`.

## 1. Find the scanner's bus/device on the host

```sh
lsusb | grep 0f05
# e.g. "Bus 001 Device 008: ID 0f05:f235 ..."  -> bus 1, device 8
```

Note the **bus** (→ usbmon interface `usbmon<bus>`) and **device number**. The
device number can change on replug; re-check before each capture.

> If `lsusb` does not show `0f05:*` on the host while the VM has it, the
> passthrough is hiding it from the host entirely (rare with QEMU/VirtualBox
> usbfs passthrough). In that case use the fallback in §5.

## 2. Start the capture on the host (full payloads → pcapng)

```sh
sudo modprobe usbmon
# capture just the scanner's bus (usbmon1 == bus 1); usbmon0 = all buses
sudo dumpcap -i usbmon1 -w pakon_scan.pcapng
```

Leave it running. (To cut noise you can capture only the device:
`sudo dumpcap -i usbmon1 -f "usb.device_address == 8" -w pakon_scan.pcapng` —
capture filter support for usb varies, so the unfiltered capture + filtering in
analysis is the safe default.)

## 3. Perform the scan in the VM, narrating timing

While dumpcap runs, do a scan in the VM and **note wall-clock moments** so we
can mark phase boundaries later:

- t0: app opened / device connected
- t1: pressed "scan" / fed film
- t2: first image appeared
- t3: scan finished

Per the plan, do **2–3 captures** with varied settings (different resolutions,
plus color and B&W) — one capture file each.

## 4. Stop, and commit the capture

`Ctrl-C` the dumpcap. Then commit each capture to `test/captures/` with a notes
file, e.g. `test/captures/scan-color-2000dpi.notes.md` recording: model,
settings, the t0..t3 narration, and anything unusual. (Large image payloads are
fine.)

## 4a. IMPORTANT: host usbmon does not capture bulk image payloads

With VirtualBox usbfs passthrough, host-side usbmon captures the **command
channel** fine (small EP1 transfers go through kernel buffers) but records
**zero payload bytes for the large bulk-IN image transfers on `0x86`** (they
show `urb.length` but `len_cap=0`, because the guest's large buffers are filled
directly in guest memory the host can't snapshot). The image is read in
20480-byte chunks, but to capture the actual image **bytes** you must capture
**inside the VM with USBPcap** (§5), which sees full payloads at the Windows
USB stack. Use host usbmon for the protocol; use in-VM USBPcap for image data.

## 5. Fallback / image data: capture inside the Windows VM

If the host can't see the device, install **Wireshark + USBPcap** inside the VM
and capture the USBPcap interface there while scanning; export to pcapng. Same
analysis applies.

## 6. Quick path without Wireshark (command handshake only)

The usbmon text node needs no Wireshark, but truncates data per URB (~32 bytes),
so it is only good for the *small* control/command exchange, not image data:

```sh
sudo modprobe usbmon
sudo cat /sys/kernel/debug/usb/usbmon/1u > pakon_handshake.txt   # while scanning
```

## 7. Analyze

```sh
# full pcapng (needs tshark on PATH):
python3 tools/analyze_capture.py pakon_scan.pcapng --bus 1 --device 8

# or a pre-extracted tshark TSV / usbmon text file:
python3 tools/analyze_capture.py pakon_handshake.txt --format usbmon
```

The analyzer filters to the scanner, prints a time-ordered URB timeline,
**decodes control-transfer setup packets (the vendor request codes we need)**,
flags 36-byte payloads and decodes the Pakon frame header, and prints a summary
of the distinct transfer/endpoint/request combinations seen. That summary is
what tells us whether commands ride EP0 control transfers (most likely) or a
bulk channel, and what the open/init sequence is.
