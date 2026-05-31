/*
 * pakon_probe — standalone libusb test harness (NOT a SANE backend).
 *
 * Phase 0: opens a transport context, asks the transport layer whether a
 * Pakon is present, and prints the result. With no device attached it must
 * print "no device" and exit 0 cleanly — that is part of the Phase 0 exit
 * criteria. The hardware-only modes are advertised in --help but are stubbed
 * until Phases 1-2.
 */
#include "pakon_usb.h"
#include "pakon_log.h"

#include <stdio.h>
#include <string.h>

static void usage(const char *argv0)
{
    fprintf(stderr,
        "usage: %s [--load-firmware] [--raw HEX] [--help]\n"
        "  (default)        enumerate and report cold/warm/no device\n"
        "  --load-firmware  download firmware to a cold device   [Phase 1]\n"
        "  --raw HEX        send a hex string, dump the reply     [Phase 2]\n"
        "\n"
        "Set PAKON_DEBUG=0..4 for increasing trace verbosity.\n",
        argv0);
}

int main(int argc, char **argv)
{
    int want_firmware = 0;
    const char *raw_hex = NULL;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) {
            usage(argv[0]);
            return 0;
        } else if (!strcmp(argv[i], "--load-firmware")) {
            want_firmware = 1;
        } else if (!strcmp(argv[i], "--raw") && i + 1 < argc) {
            raw_hex = argv[++i];
        } else {
            fprintf(stderr, "unknown argument: %s\n", argv[i]);
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
    pakon_dev_class cls = PAKON_DEV_UNKNOWN;
    r = pakon_usb_find(ctx, &cls);

    if (r == PAKON_ERR_NO_DEVICE) {
        printf("no device\n");
    } else if (r == PAKON_OK && cls == PAKON_DEV_WARM) {
        printf("found warm Pakon (%04x:%04x)\n",
               PAKON_WARM_VID, PAKON_WARM_PID);
    } else if (r == PAKON_OK && cls == PAKON_DEV_COLD) {
        printf("found cold FX2 device (needs firmware)\n");
    } else {
        printf("enumeration: %s\n", pakon_result_str(r));
        exit_code = 1;
    }

    if (want_firmware) {
        printf("--load-firmware: not yet implemented (Phase 1)\n");
        exit_code = 1;
    }
    if (raw_hex) {
        printf("--raw: not yet implemented (Phase 2)\n");
        exit_code = 1;
    }

    pakon_usb_exit(ctx);
    return exit_code;
}
