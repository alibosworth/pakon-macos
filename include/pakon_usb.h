/*
 * pakon_usb.h — the transport layer.
 *
 * Owns the libusb context, device enumeration/classification, firmware
 * download, interface claiming, and raw bulk send/recv. It knows nothing about
 * Pakon packet framing (no pakon_proto types here) and nothing about SANE.
 * The only shared vocabulary with the protocol layer is pakon_result, which
 * lives in pakon_log.h.
 *
 * Everything below is declared for the full design but is STUBBED in Phase 0:
 * enumeration, firmware load, and bulk I/O require hardware (Phases 1-2) and
 * return PAKON_ERR_UNIMPLEMENTED until then.
 */
#ifndef PAKON_USB_H
#define PAKON_USB_H

#include <stdint.h>
#include <stddef.h>

#include "pakon_log.h"   /* for pakon_result */

#ifdef __cplusplus
extern "C" {
#endif

/* Warm (post-firmware) Pakon identity — documented, treated as known-good. */
#define PAKON_WARM_VID   0x0F05
#define PAKON_WARM_PID   0xF135

/*
 * Cold (pre-firmware, FX2 bootloader) identity. UNKNOWN until confirmed via
 * lsusb on a freshly powered scanner — see STOP POINT A in Phase 1. These are
 * deliberately left as 0 so nothing accidentally matches a guessed value.
 */
#define PAKON_COLD_VID   0x0000
#define PAKON_COLD_PID   0x0000

typedef enum {
    PAKON_DEV_UNKNOWN = 0,
    PAKON_DEV_COLD,    /* FX2 bootloader, needs firmware download */
    PAKON_DEV_WARM     /* 0F05:F135, ready for the protocol layer */
} pakon_dev_class;

/* Summary of one device on the bus, for the diagnostic listing that helps
 * confirm the cold VID/PID at STOP POINT A. */
typedef struct {
    uint16_t vid;
    uint16_t pid;
    uint8_t  bus;
    uint8_t  address;
    uint8_t  dev_class;     /* bDeviceClass */
} pakon_usb_devinfo;

/* Opaque transport context (wraps libusb_context). */
typedef struct pakon_ctx pakon_ctx;

/* Opaque open-device handle (wraps libusb_device_handle + endpoint map). */
typedef struct pakon_dev pakon_dev;

/* Describes one of the warm device's endpoints, for the Phase 1 dump. */
typedef struct {
    uint8_t address;        /* bEndpointAddress (dir bit included) */
    uint8_t attributes;     /* bmAttributes (transfer type) */
    uint16_t max_packet;    /* wMaxPacketSize */
} pakon_endpoint;

/* ---- context lifecycle ---- */

pakon_result pakon_usb_init(pakon_ctx **out_ctx);
void         pakon_usb_exit(pakon_ctx *ctx);

/* ---- enumeration / classification (Phase 1) ---- */

/*
 * Scan the bus and report whether a cold or warm Pakon is present.
 * `out_class` is set to PAKON_DEV_UNKNOWN when nothing matches; the call then
 * returns PAKON_ERR_NO_DEVICE. A warm match uses the documented 0F05:F135
 * identity; a cold match is only possible once PAKON_COLD_VID/PID is filled in
 * after STOP POINT A (it is 0 until then, so cold never matches yet).
 */
pakon_result pakon_usb_find(pakon_ctx *ctx, pakon_dev_class *out_class);

/*
 * Diagnostic: copy up to `max` device summaries for everything on the bus into
 * `arr`, writing the real count to `*out_count`. Used by `pakon_probe --list`
 * to identify the scanner's cold VID/PID before it is hardcoded anywhere.
 */
pakon_result pakon_usb_list(pakon_ctx *ctx, pakon_usb_devinfo *arr,
                            size_t max, size_t *out_count);

/* ---- firmware download (Phase 1) ---- */

/*
 * Download an Intel HEX firmware image to a cold FX2 device and wait for it to
 * re-enumerate as the warm Pakon. STUB. `hex_path` is the .hex in firmware/.
 */
pakon_result pakon_usb_load_firmware(pakon_ctx *ctx, const char *hex_path);

/* ---- open / close (Phase 1-2) ---- */

pakon_result pakon_usb_open(pakon_ctx *ctx, pakon_dev **out_dev);
void         pakon_usb_close(pakon_dev *dev);

/*
 * Copy up to `max` endpoint descriptors of the open device into `eps`; the
 * actual count is written to `*out_count`. STUB.
 */
pakon_result pakon_usb_endpoints(pakon_dev *dev, pakon_endpoint *eps,
                                 size_t max, size_t *out_count);

/* ---- raw bulk I/O (Phase 2) ---- */

/*
 * Send exactly `len` bytes on the command endpoint / receive up to `len` bytes
 * from the bulk-IN endpoint. Exact sizing, libusb timeouts, and full hexdump
 * tracing (via pakon_log) are part of the contract. STUB until Phase 2.
 */
pakon_result pakon_usb_send(pakon_dev *dev, const uint8_t *buf, size_t len,
                            unsigned timeout_ms);
pakon_result pakon_usb_recv(pakon_dev *dev, uint8_t *buf, size_t len,
                            size_t *out_received, unsigned timeout_ms);

#ifdef __cplusplus
}
#endif

#endif /* PAKON_USB_H */
