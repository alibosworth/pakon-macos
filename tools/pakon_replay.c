/*
 * pakon_replay — replays/extends captured handshakes against the device.
 *
 * This is where the documented open handshake (Phase 3) and the discovered
 * scan state machine (Phase 5) will be driven from. Phase 0 is just a stub
 * that parses its mode flag and reports that the hardware work is pending, so
 * the build produces the binary and the CLI surface is pinned down early.
 */
#include "pakon_usb.h"
#include "pakon_proto.h"
#include "pakon_log.h"

#include <stdio.h>
#include <string.h>

static void usage(const char *argv0)
{
    fprintf(stderr,
        "usage: %s [--open] [--scan] [--help]\n"
        "  --open   drive the documented open handshake to Idle   [Phase 3]\n"
        "  --scan   run the scan state machine, write an image     [Phase 5]\n"
        "\n"
        "Set PAKON_DEBUG=0..4 for increasing trace verbosity.\n",
        argv0);
}

int main(int argc, char **argv)
{
    int want_open = 0, want_scan = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) {
            usage(argv[0]);
            return 0;
        } else if (!strcmp(argv[i], "--open")) {
            want_open = 1;
        } else if (!strcmp(argv[i], "--scan")) {
            want_scan = 1;
        } else {
            fprintf(stderr, "unknown argument: %s\n", argv[i]);
            usage(argv[0]);
            return 2;
        }
    }

    if (!want_open && !want_scan) {
        usage(argv[0]);
        return 2;
    }

    if (want_open)
        printf("--open: not yet implemented (Phase 3)\n");
    if (want_scan)
        printf("--scan: not yet implemented (Phase 5)\n");

    return 1;
}
