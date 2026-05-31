/*
 * test_proto — hardware-free unit tests for the framing layer.
 *
 * Phase 0 deliverable: assert the wire struct is exactly 36 bytes and that its
 * fields land at the documented offsets. Checksum round-trip tests are added
 * in Phase 3 once pakon_checksum is real (they are sketched here as a skipped
 * placeholder so the harness is ready).
 */
#include "pakon_proto.h"

#include <stdio.h>
#include <stddef.h>

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
        if (cond) {                                             \
            printf("ok   - %s\n", (msg));                       \
        } else {                                                \
            printf("FAIL - %s\n", (msg));                       \
            failures++;                                         \
        }                                                       \
    } while (0)

int main(void)
{
    /* The core Phase 0 assertion. */
    CHECK(sizeof(pakon_packet) == 36, "pakon_packet is 36 bytes");
    CHECK(PAKON_PACKET_SIZE == 36, "PAKON_PACKET_SIZE == 36");
    CHECK(PAKON_DATA_MAX == 34, "PAKON_DATA_MAX == 34");

    /* Field offsets match the documented frame layout. */
    CHECK(offsetof(pakon_packet, type) == 0, "type at byte 0");
    CHECK(offsetof(pakon_packet, count) == 1, "count at byte 1");
    CHECK(offsetof(pakon_packet, data) == 2, "data starts at byte 2");

    /* Documented enum values are wired up correctly. */
    CHECK(AD_HOST == 0x10, "AD_HOST == 0x10");
    CHECK(AD_BOOT_PICM_PLUS == 0x46, "AD_BOOT_PICM_PLUS == 0x46");
    CHECK(PS_SUCCESS == 0, "PS_SUCCESS == 0");
    CHECK(PS_BUS_ERROR == 9, "PS_BUS_ERROR == 9");

    /* TODO(Phase 3): checksum round-trip against known-good packets such as
     * the open packet 04 03 10 00 85. pakon_checksum is a stub for now. */
    printf("# skip - checksum round-trip (Phase 3, pakon_checksum stubbed)\n");

    if (failures) {
        printf("\n%d test(s) FAILED\n", failures);
        return 1;
    }
    printf("\nall tests passed\n");
    return 0;
}
