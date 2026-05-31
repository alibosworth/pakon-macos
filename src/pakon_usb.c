/*
 * pakon_usb.c — transport layer (Phase 1, up to STOP POINT A).
 *
 * Implemented and exercisable without the cold VID/PID:
 *   - libusb context lifecycle
 *   - real bus enumeration + warm (0F05:F135) classification
 *   - a full-bus diagnostic listing (pakon_usb_list) to identify the cold
 *     device at STOP POINT A
 *   - opening a warm device and dumping its endpoint map
 *   - the FX2 RAM-download mechanism (standard EZ-USB protocol) and Intel HEX
 *     plumbing, wired together but GATED: it refuses to run until PAKON_COLD_*
 *     is filled in, so no cold VID/PID is ever guessed.
 *
 * Bulk send/recv remain Phase 2.
 */
#include "pakon_usb.h"
#include "pakon_hex.h"

#include <stdlib.h>
#include <string.h>
#include <libusb.h>

/* --- FX2 (EZ-USB) firmware-download constants (standard, not invented) --- */
#define FX2_VENDOR_RW   0xA0      /* vendor request: read/write internal RAM */
#define FX2_CPUCS       0xE600    /* 8051 control reg; bit0 = hold-in-reset */
#define FX2_RAM_MAX     0x4000    /* internal RAM extent for the data path */
#define FX2_TIMEOUT_MS  2000

#define MAX_ENDPOINTS   8

struct pakon_ctx {
    libusb_context *usb;
};

struct pakon_dev {
    libusb_device_handle *handle;
    uint16_t vid;
    uint16_t pid;
    pakon_endpoint endpoints[MAX_ENDPOINTS];
    size_t n_endpoints;
    /* claimed interface, etc. filled in Phase 2 */
};

pakon_result pakon_usb_init(pakon_ctx **out_ctx)
{
    if (!out_ctx)
        return PAKON_ERR_PARAM;

    pakon_ctx *ctx = (pakon_ctx *)calloc(1, sizeof(*ctx));
    if (!ctx)
        return PAKON_ERR_USB;

    int rc = libusb_init(&ctx->usb);
    if (rc != 0) {
        pakon_logf(PAKON_LOG_ERROR, "libusb_init failed: %s",
                   libusb_strerror((enum libusb_error)rc));
        free(ctx);
        return PAKON_ERR_USB;
    }

    pakon_logf(PAKON_LOG_DEBUG, "libusb context initialized");
    *out_ctx = ctx;
    return PAKON_OK;
}

void pakon_usb_exit(pakon_ctx *ctx)
{
    if (!ctx)
        return;
    if (ctx->usb)
        libusb_exit(ctx->usb);
    free(ctx);
}

int pakon_is_warm_id(uint16_t vid, uint16_t pid)
{
    return vid == PAKON_WARM_VID &&
           (pid == PAKON_WARM_PID_F135 ||
            pid == PAKON_WARM_PID_F235 ||
            pid == PAKON_WARM_PID_F335);
}

static int is_warm(const struct libusb_device_descriptor *d)
{
    return pakon_is_warm_id(d->idVendor, d->idProduct);
}

/* Cold match is only meaningful once the IDs are confirmed (post STOP POINT A).
 * Until then PAKON_COLD_VID/PID are 0 and this never matches anything. */
static int is_cold(const struct libusb_device_descriptor *d)
{
    if (PAKON_COLD_VID == 0 && PAKON_COLD_PID == 0)
        return 0;
    return d->idVendor == PAKON_COLD_VID && d->idProduct == PAKON_COLD_PID;
}

pakon_result pakon_usb_find(pakon_ctx *ctx, pakon_dev_class *out_class)
{
    if (!ctx || !out_class)
        return PAKON_ERR_PARAM;

    *out_class = PAKON_DEV_UNKNOWN;

    libusb_device **list = NULL;
    ssize_t n = libusb_get_device_list(ctx->usb, &list);
    if (n < 0) {
        pakon_logf(PAKON_LOG_ERROR, "get_device_list: %s",
                   libusb_strerror((enum libusb_error)n));
        return PAKON_ERR_USB;
    }

    pakon_dev_class found = PAKON_DEV_UNKNOWN;
    for (ssize_t i = 0; i < n; i++) {
        struct libusb_device_descriptor d;
        if (libusb_get_device_descriptor(list[i], &d) != 0)
            continue;
        if (is_warm(&d)) {
            found = PAKON_DEV_WARM;
            break;                  /* warm wins outright */
        }
        if (is_cold(&d))
            found = PAKON_DEV_COLD;
    }
    libusb_free_device_list(list, 1);

    if (found == PAKON_DEV_UNKNOWN) {
        pakon_logf(PAKON_LOG_DEBUG, "no Pakon device found");
        return PAKON_ERR_NO_DEVICE;
    }

    pakon_logf(PAKON_LOG_INFO, "found %s Pakon device",
               found == PAKON_DEV_WARM ? "warm" : "cold");
    *out_class = found;
    return PAKON_OK;
}

pakon_result pakon_usb_list(pakon_ctx *ctx, pakon_usb_devinfo *arr,
                            size_t max, size_t *out_count)
{
    if (!ctx || !out_count)
        return PAKON_ERR_PARAM;
    *out_count = 0;

    libusb_device **list = NULL;
    ssize_t n = libusb_get_device_list(ctx->usb, &list);
    if (n < 0) {
        pakon_logf(PAKON_LOG_ERROR, "get_device_list: %s",
                   libusb_strerror((enum libusb_error)n));
        return PAKON_ERR_USB;
    }

    size_t count = 0;
    for (ssize_t i = 0; i < n; i++) {
        struct libusb_device_descriptor d;
        if (libusb_get_device_descriptor(list[i], &d) != 0)
            continue;
        if (arr && count < max) {
            arr[count].vid       = d.idVendor;
            arr[count].pid       = d.idProduct;
            arr[count].bus       = libusb_get_bus_number(list[i]);
            arr[count].address   = libusb_get_device_address(list[i]);
            arr[count].dev_class = d.bDeviceClass;
        }
        count++;
    }
    libusb_free_device_list(list, 1);

    *out_count = count;
    return PAKON_OK;
}

/* Collect the endpoint map of the open device's first interface/altsetting. */
static pakon_result cache_endpoints(pakon_dev *dev, libusb_device *udev)
{
    struct libusb_config_descriptor *cfg = NULL;
    int rc = libusb_get_active_config_descriptor(udev, &cfg);
    if (rc != 0) {
        pakon_logf(PAKON_LOG_ERROR, "get_active_config: %s",
                   libusb_strerror((enum libusb_error)rc));
        return PAKON_ERR_USB;
    }

    dev->n_endpoints = 0;
    if (cfg->bNumInterfaces > 0 && cfg->interface[0].num_altsetting > 0) {
        const struct libusb_interface_descriptor *id =
            &cfg->interface[0].altsetting[0];
        for (uint8_t e = 0; e < id->bNumEndpoints &&
             dev->n_endpoints < MAX_ENDPOINTS; e++) {
            const struct libusb_endpoint_descriptor *ep = &id->endpoint[e];
            dev->endpoints[dev->n_endpoints].address    = ep->bEndpointAddress;
            dev->endpoints[dev->n_endpoints].attributes  = ep->bmAttributes;
            dev->endpoints[dev->n_endpoints].max_packet  = ep->wMaxPacketSize;
            dev->n_endpoints++;
        }
    }
    libusb_free_config_descriptor(cfg);
    return PAKON_OK;
}

pakon_result pakon_usb_open(pakon_ctx *ctx, pakon_dev **out_dev)
{
    if (!ctx || !out_dev)
        return PAKON_ERR_PARAM;
    *out_dev = NULL;

    libusb_device **list = NULL;
    ssize_t n = libusb_get_device_list(ctx->usb, &list);
    if (n < 0)
        return PAKON_ERR_USB;

    libusb_device *match = NULL;
    struct libusb_device_descriptor md = {0};
    for (ssize_t i = 0; i < n; i++) {
        struct libusb_device_descriptor d;
        if (libusb_get_device_descriptor(list[i], &d) != 0)
            continue;
        if (is_warm(&d)) {
            match = list[i];
            md = d;
            break;
        }
    }

    if (!match) {
        libusb_free_device_list(list, 1);
        pakon_logf(PAKON_LOG_DEBUG, "no warm device to open");
        return PAKON_ERR_NO_DEVICE;
    }

    pakon_dev *dev = (pakon_dev *)calloc(1, sizeof(*dev));
    if (!dev) {
        libusb_free_device_list(list, 1);
        return PAKON_ERR_USB;
    }
    dev->vid = md.idVendor;
    dev->pid = md.idProduct;

    int rc = libusb_open(match, &dev->handle);
    if (rc != 0) {
        pakon_logf(PAKON_LOG_ERROR, "libusb_open: %s",
                   libusb_strerror((enum libusb_error)rc));
        free(dev);
        libusb_free_device_list(list, 1);
        return PAKON_ERR_USB;
    }

    pakon_result r = cache_endpoints(dev, match);
    libusb_free_device_list(list, 1);
    if (r != PAKON_OK) {
        libusb_close(dev->handle);
        free(dev);
        return r;
    }

    /* Interface claiming + macOS class-driver detach is Phase 2. */
    *out_dev = dev;
    return PAKON_OK;
}

void pakon_usb_close(pakon_dev *dev)
{
    if (!dev)
        return;
    if (dev->handle)
        libusb_close(dev->handle);
    free(dev);
}

void pakon_usb_dev_ids(const pakon_dev *dev, uint16_t *vid, uint16_t *pid)
{
    if (!dev)
        return;
    if (vid) *vid = dev->vid;
    if (pid) *pid = dev->pid;
}

pakon_result pakon_usb_endpoints(pakon_dev *dev, pakon_endpoint *eps,
                                 size_t max, size_t *out_count)
{
    if (!dev || !out_count)
        return PAKON_ERR_PARAM;
    *out_count = dev->n_endpoints;
    if (eps) {
        size_t copy = dev->n_endpoints < max ? dev->n_endpoints : max;
        memcpy(eps, dev->endpoints, copy * sizeof(*eps));
    }
    return PAKON_OK;
}

/* ------------------------------------------------------------------ */
/* FX2 firmware download (Phase 1 task 2). Mechanism is the standard   */
/* EZ-USB protocol; it is GATED behind a confirmed cold VID/PID so it  */
/* cannot run against a guessed device.                                */
/* ------------------------------------------------------------------ */

/* Write `len` bytes to FX2 internal RAM at `addr` via vendor request 0xA0. */
static pakon_result fx2_write_ram(libusb_device_handle *h, uint16_t addr,
                                  const uint8_t *data, uint16_t len)
{
    int rc = libusb_control_transfer(
        h,
        LIBUSB_ENDPOINT_OUT | LIBUSB_REQUEST_TYPE_VENDOR | LIBUSB_RECIPIENT_DEVICE,
        FX2_VENDOR_RW, addr, 0,
        (unsigned char *)data, len, FX2_TIMEOUT_MS);
    if (rc < 0) {
        pakon_logf(PAKON_LOG_ERROR, "fx2 write @%04x: %s", addr,
                   libusb_strerror((enum libusb_error)rc));
        return PAKON_ERR_USB;
    }
    pakon_hexdump(PAKON_LOG_TRACE, "fx2 write", data, len);
    return PAKON_OK;
}

/* Assert (reset=1) or release (reset=0) the 8051 by writing CPUCS. */
static pakon_result fx2_reset(libusb_device_handle *h, int hold)
{
    uint8_t v = hold ? 0x01 : 0x00;
    return fx2_write_ram(h, FX2_CPUCS, &v, 1);
}

/* Validation-only callback (no hardware): checks each record fits in RAM. */
static pakon_result fx2_validate_cb(uint32_t addr, const uint8_t *data,
                                    uint8_t len, void *user)
{
    (void)data; (void)user;
    if (addr + len > FX2_RAM_MAX) {
        pakon_logf(PAKON_LOG_WARN,
                   "fx2: record @%04x+%u exceeds expected RAM extent",
                   addr, len);
    }
    return PAKON_OK;
}

/* Download callback: each HEX data record becomes one RAM write. */
static pakon_result fx2_record_cb(uint32_t addr, const uint8_t *data,
                                  uint8_t len, void *user)
{
    libusb_device_handle *h = (libusb_device_handle *)user;
    return fx2_write_ram(h, (uint16_t)addr, data, len);
}

pakon_result pakon_usb_load_firmware(pakon_ctx *ctx, const char *hex_path)
{
    if (!ctx || !hex_path)
        return PAKON_ERR_PARAM;

    /*
     * STOP POINT A gate: we will not download firmware until the cold FX2
     * VID/PID is confirmed from real hardware and filled into PAKON_COLD_*.
     * Targeting a guessed device could write 0xA0 control transfers to the
     * wrong peripheral, so this is a hard stop, not a warning.
     */
    if (PAKON_COLD_VID == 0 && PAKON_COLD_PID == 0) {
        pakon_logf(PAKON_LOG_ERROR,
                   "cold FX2 VID/PID not configured — complete STOP POINT A "
                   "(plug in the cold scanner, run lsusb/system_profiler, and "
                   "set PAKON_COLD_VID/PID in pakon_usb.h) before loading "
                   "firmware");
        return PAKON_ERR_NO_DEVICE;
    }

    /* Validate the HEX up front (hardware-free) before touching the bus. */
    pakon_result r = pakon_hex_parse_file(hex_path, fx2_validate_cb, NULL);
    if (r != PAKON_OK) {
        pakon_logf(PAKON_LOG_ERROR, "firmware HEX failed validation: %s",
                   pakon_result_str(r));
        return r;
    }

    /* Open the cold device. */
    libusb_device_handle *h =
        libusb_open_device_with_vid_pid(ctx->usb, PAKON_COLD_VID, PAKON_COLD_PID);
    if (!h) {
        pakon_logf(PAKON_LOG_ERROR, "cannot open cold FX2 device %04x:%04x",
                   PAKON_COLD_VID, PAKON_COLD_PID);
        return PAKON_ERR_NO_DEVICE;
    }

    /* Hold 8051 in reset, stream the firmware, release. */
    r = fx2_reset(h, /*hold=*/1);
    if (r == PAKON_OK)
        r = pakon_hex_parse_file(hex_path, fx2_record_cb, h);
    if (r == PAKON_OK)
        r = fx2_reset(h, /*hold=*/0);

    libusb_close(h);

    if (r != PAKON_OK) {
        pakon_logf(PAKON_LOG_ERROR, "firmware download failed: %s",
                   pakon_result_str(r));
        return r;
    }

    pakon_logf(PAKON_LOG_INFO,
               "firmware sent; waiting for re-enumeration to a warm %04x:Fx35",
               PAKON_WARM_VID);
    /* Re-enumeration polling is finished in the post-STOP-POINT-A work. */
    return PAKON_OK;
}

pakon_result pakon_usb_send(pakon_dev *dev, const uint8_t *buf, size_t len,
                            unsigned timeout_ms)
{
    (void)dev; (void)buf; (void)len; (void)timeout_ms;
    return PAKON_ERR_UNIMPLEMENTED;   /* Phase 2 */
}

pakon_result pakon_usb_recv(pakon_dev *dev, uint8_t *buf, size_t len,
                            size_t *out_received, unsigned timeout_ms)
{
    (void)dev; (void)buf; (void)len; (void)timeout_ms;
    if (out_received)
        *out_received = 0;
    return PAKON_ERR_UNIMPLEMENTED;   /* Phase 2 */
}
