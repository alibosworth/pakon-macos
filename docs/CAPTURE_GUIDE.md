# Capture guide (stub — fleshed out in Phase 4)

This document will describe how to capture a full, ground-truth scan session
under the working Windows driver so the undocumented scan path can be decoded.

Planned contents (Phase 4):

- **Windows + Wireshark/USBPcap:** run a full scan under Kaufman's 64-bit driver
  + TLXClientDemo while capturing the USB bus; export to pcap.
- **Linux + usbmon + Wireshark:** alternative capture if a Linux host runs the
  Windows VM with USB passthrough.
- Capture matrix: 2–3 scans at different resolutions, color + B&W.
- Where captures land: `test/captures/`, each with notes on exactly what action
  was performed when (so phase boundaries — calibration start, first image
  bytes, end — can be marked).

The companion capture-analysis tool (ingests a pcap/text export, filters to our
endpoints, emits annotated packet sequences) is also written in Phase 4.
