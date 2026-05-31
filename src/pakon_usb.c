/*
 * pakon_usb.c — transport layer.
 *
 * Phase 0: the libusb context lifecycle is real (so pakon_probe can open a
 * context and cleanly report "no device"), but everything that touches a
 * specific device — enumeration/classification, firmware download, bulk I/O —
 * is stubbed until the hardware phases (1-2).
 */
#include "pakon_usb.h"

#include <stdlib.h>
#include <libusb.h>

struct pakon_ctx {
    libusb_context *usb;
};

struct pakon_dev {
    libusb_device_handle *handle;
    /* endpoint map, claimed interface, etc. filled in Phase 1-2 */
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

pakon_result pakon_usb_find(pakon_ctx *ctx, pakon_dev_class *out_class)
{
    if (!ctx || !out_class)
        return PAKON_ERR_PARAM;

    *out_class = PAKON_DEV_UNKNOWN;

    /*
     * Real classification (Phase 1) will walk libusb_get_device_list and match
     * against PAKON_WARM_VID/PID and the (still-unknown) cold FX2 ID. For now
     * we only ever report "no device" so the probe exits cleanly.
     */
    pakon_logf(PAKON_LOG_DEBUG,
               "pakon_usb_find: enumeration not yet implemented (Phase 1)");
    return PAKON_ERR_NO_DEVICE;
}

pakon_result pakon_usb_load_firmware(pakon_ctx *ctx, const char *hex_path)
{
    (void)ctx; (void)hex_path;
    return PAKON_ERR_UNIMPLEMENTED;   /* Phase 1 */
}

pakon_result pakon_usb_open(pakon_ctx *ctx, pakon_dev **out_dev)
{
    if (!ctx || !out_dev)
        return PAKON_ERR_PARAM;
    *out_dev = NULL;
    return PAKON_ERR_NO_DEVICE;       /* Phase 1-2 */
}

void pakon_usb_close(pakon_dev *dev)
{
    if (!dev)
        return;
    if (dev->handle)
        libusb_close(dev->handle);
    free(dev);
}

pakon_result pakon_usb_endpoints(pakon_dev *dev, pakon_endpoint *eps,
                                 size_t max, size_t *out_count)
{
    (void)dev; (void)eps; (void)max;
    if (out_count)
        *out_count = 0;
    return PAKON_ERR_UNIMPLEMENTED;   /* Phase 1 */
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
