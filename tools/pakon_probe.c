/*
 * pakon_probe — standalone libusb test harness (NOT a SANE backend).
 *
 * Phase 1 (up to STOP POINT A):
 *   (default)         enumerate; report warm / cold / no device. With a warm
 *                     device present, open it and print its endpoint map.
 *   --list            dump every USB device on the bus (VID:PID, bus/addr,
 *                     class) with hints — use this to identify the scanner's
 *                     COLD VID/PID before any driver loads (STOP POINT A).
 *   --load-firmware   download firmware to a cold device (gated until the cold
 *                     VID/PID is confirmed and filled into pakon_usb.h).
 *   --raw HEX         Phase 2.
 */
#include "pakon_usb.h"
#include "pakon_log.h"

#include <stdio.h>
#include <string.h>

static void usage(const char *argv0)
{
    fprintf(stderr,
        "usage: %s [--list] [--load-firmware HEX] [--raw HEX] [--help]\n"
        "  (default)         enumerate; if warm, print the endpoint map\n"
        "  --list            dump all USB devices (for STOP POINT A)\n"
        "  --load-firmware HEX  download firmware to a cold device  [gated]\n"
        "  --raw HEX         send a hex string, dump the reply       [Phase 2]\n"
        "\n"
        "Set PAKON_DEBUG=0..4 for increasing trace verbosity.\n",
        argv0);
}

/* Best-effort hint to help spot the cold FX2 bootloader in --list output.
 * Diagnostic only — NOT used by any matching/classification logic. */
static const char *device_hint(unsigned vid, unsigned pid)
{
    if (vid == PAKON_WARM_VID && pid == PAKON_WARM_PID)
        return "  <-- warm Pakon (0F05:F135)";
    if (vid == 0x04b4 || vid == 0x0547)
        return "  <-- Cypress/Anchor vendor: possible FX2 bootloader?";
    return "";
}

static int do_list(pakon_ctx *ctx)
{
    pakon_usb_devinfo devs[128];
    size_t count = 0;
    pakon_result r = pakon_usb_list(ctx, devs, 128, &count);
    if (r != PAKON_OK) {
        fprintf(stderr, "list failed: %s\n", pakon_result_str(r));
        return 1;
    }

    printf("%zu USB device(s):\n", count);
    printf("  bus addr  vid:pid    class\n");
    size_t shown = count < 128 ? count : 128;
    for (size_t i = 0; i < shown; i++) {
        printf("  %3u %4u  %04x:%04x  0x%02x%s\n",
               devs[i].bus, devs[i].address, devs[i].vid, devs[i].pid,
               devs[i].dev_class, device_hint(devs[i].vid, devs[i].pid));
    }
    printf("\nFor STOP POINT A: power-cycle the scanner, run this BEFORE any\n"
           "driver loads, and note the cold device's vid:pid.\n");
    return 0;
}

static const char *xfer_type(unsigned attr)
{
    switch (attr & 0x03) {
    case 0: return "control";
    case 1: return "isochronous";
    case 2: return "bulk";
    case 3: return "interrupt";
    }
    return "?";
}

static int dump_warm_endpoints(pakon_ctx *ctx)
{
    pakon_dev *dev = NULL;
    pakon_result r = pakon_usb_open(ctx, &dev);
    if (r != PAKON_OK) {
        fprintf(stderr, "open warm device failed: %s\n", pakon_result_str(r));
        return 1;
    }

    pakon_endpoint eps[8];
    size_t n = 0;
    r = pakon_usb_endpoints(dev, eps, 8, &n);
    if (r != PAKON_OK) {
        fprintf(stderr, "endpoint query failed: %s\n", pakon_result_str(r));
        pakon_usb_close(dev);
        return 1;
    }

    printf("%zu endpoint(s):\n", n);
    for (size_t i = 0; i < n && i < 8; i++) {
        printf("  ep 0x%02x  %-11s  %s  max %u\n",
               eps[i].address, xfer_type(eps[i].attributes),
               (eps[i].address & 0x80) ? "IN " : "OUT",
               eps[i].max_packet);
    }
    pakon_usb_close(dev);
    return 0;
}

int main(int argc, char **argv)
{
    int want_list = 0;
    const char *firmware_hex = NULL;
    const char *raw_hex = NULL;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) {
            usage(argv[0]);
            return 0;
        } else if (!strcmp(argv[i], "--list")) {
            want_list = 1;
        } else if (!strcmp(argv[i], "--load-firmware") && i + 1 < argc) {
            firmware_hex = argv[++i];
        } else if (!strcmp(argv[i], "--raw") && i + 1 < argc) {
            raw_hex = argv[++i];
        } else {
            fprintf(stderr, "unknown/incomplete argument: %s\n", argv[i]);
            usage(argv[0]);
            return 2;
        }
    }

    pakon_ctx *ctx = NULL;
    pakon_result r = pakon_usb_init(&ctx);
    if (r != PAKON_OK) {
        fprintf(stderr, "init failed: %s\n", pakon_result_str(r));
        return 1;
    }

    int exit_code = 0;

    if (want_list) {
        exit_code = do_list(ctx);
        pakon_usb_exit(ctx);
        return exit_code;
    }

    if (firmware_hex) {
        r = pakon_usb_load_firmware(ctx, firmware_hex);
        if (r != PAKON_OK) {
            fprintf(stderr, "load-firmware: %s\n", pakon_result_str(r));
            exit_code = 1;
        } else {
            printf("load-firmware: firmware sent\n");
        }
        pakon_usb_exit(ctx);
        return exit_code;
    }

    if (raw_hex) {
        printf("--raw: not yet implemented (Phase 2)\n");
        pakon_usb_exit(ctx);
        return 1;
    }

    /* Default: classify, and if warm, dump the endpoint map. */
    pakon_dev_class cls = PAKON_DEV_UNKNOWN;
    r = pakon_usb_find(ctx, &cls);
    if (r == PAKON_ERR_NO_DEVICE) {
        printf("no device\n");
    } else if (r == PAKON_OK && cls == PAKON_DEV_WARM) {
        printf("found warm Pakon (%04x:%04x)\n",
               PAKON_WARM_VID, PAKON_WARM_PID);
        exit_code = dump_warm_endpoints(ctx);
    } else if (r == PAKON_OK && cls == PAKON_DEV_COLD) {
        printf("found cold FX2 device (needs firmware)\n");
    } else {
        printf("enumeration: %s\n", pakon_result_str(r));
        exit_code = 1;
    }

    pakon_usb_exit(ctx);
    return exit_code;
}
