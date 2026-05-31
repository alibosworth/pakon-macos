/*
 * backend/pakon.c — SANE backend shim (Phase 6).
 *
 * Placeholder only. This file is NOT part of the Phase 0 build: the SANE entry
 * points are implemented in Phase 6 once the protocol/scan layers work, and at
 * that point this target links against an installed sane-backends. Listed here
 * so the repository layout matches the plan from the start.
 *
 * When implemented, each entry point will delegate strictly downward:
 *   sane_init/sane_exit        -> pakon_usb_init / pakon_usb_exit
 *   sane_get_devices           -> pakon_usb_find (+ firmware load cold devices)
 *   sane_open/sane_close       -> pakon_usb_open + open handshake to Idle
 *   sane_get/control_option    -> minimal: resolution + mode first
 *   sane_get_parameters        -> geometry/depth from selected resolution
 *   sane_start                 -> CONFIGURE -> CALIBRATE -> SCAN
 *   sane_read                  -> stream image bytes in chunks
 *   sane_cancel                -> drive CANCEL transitions safely
 */
