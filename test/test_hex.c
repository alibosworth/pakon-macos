/*
 * test_hex — hardware-free unit tests for the Intel HEX parser.
 *
 * Covers single-line parsing (good + corrupted), checksum validation, and
 * whole-buffer iteration including extended-linear-address base handling and
 * EOF termination. No libusb, no hardware.
 */
#include "pakon_hex.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
        if (cond) { printf("ok   - %s\n", (msg)); }             \
        else { printf("FAIL - %s\n", (msg)); failures++; }      \
    } while (0)

/* Collects callback invocations so iteration can be asserted. */
struct collected {
    int n;
    uint32_t addr[16];
    uint8_t  first[16];
    uint8_t  len[16];
};

static pakon_result collect_cb(uint32_t addr, const uint8_t *data,
                               uint8_t len, void *user)
{
    struct collected *c = (struct collected *)user;
    if (c->n < 16) {
        c->addr[c->n]  = addr;
        c->first[c->n] = len ? data[0] : 0;
        c->len[c->n]   = len;
        c->n++;
    }
    return PAKON_OK;
}

int main(void)
{
    pakon_hex_record rec;

    /* Good data record: addr 0x0000, 2 bytes AA BB, checksum 0x99. */
    CHECK(pakon_hex_parse_line(":02000000AABB99", &rec) == PAKON_OK,
          "valid data line parses");
    CHECK(rec.type == PAKON_HEX_DATA && rec.len == 2 &&
          rec.addr == 0x0000 && rec.data[0] == 0xAA && rec.data[1] == 0xBB,
          "data line fields decoded");

    /* EOF record. */
    CHECK(pakon_hex_parse_line(":00000001FF", &rec) == PAKON_OK &&
          rec.type == PAKON_HEX_EOF, "EOF line parses");

    /* Corrupted checksum is rejected. */
    CHECK(pakon_hex_parse_line(":02000000AABB00", &rec) == PAKON_ERR_FIRMWARE,
          "bad checksum rejected");

    /* Missing ':' is rejected. */
    CHECK(pakon_hex_parse_line("02000000AABB99", &rec) == PAKON_ERR_FIRMWARE,
          "missing colon rejected");

    /* Truncated line is rejected. */
    CHECK(pakon_hex_parse_line(":02000000AA", &rec) == PAKON_ERR_FIRMWARE,
          "truncated line rejected");

    /* Leading/trailing whitespace + CRLF tolerated. */
    CHECK(pakon_hex_parse_line("  :00000001FF\r\n", &rec) == PAKON_OK,
          "surrounding whitespace tolerated");

    /* Whole-buffer iteration: two data records then EOF. */
    {
        const char *img =
            ":02000000AABB99\n"
            ":020002001122C9\n"   /* addr 0x0002, bytes 11 22, cksum C9 */
            ":00000001FF\n";
        struct collected c = {0};
        pakon_result r = pakon_hex_parse_mem(img, strlen(img), collect_cb, &c);
        CHECK(r == PAKON_OK, "multi-record buffer parses to EOF");
        CHECK(c.n == 2, "two data records seen");
        CHECK(c.n == 2 && c.addr[0] == 0x0000 && c.addr[1] == 0x0002,
              "record addresses correct");
        CHECK(c.n == 2 && c.first[0] == 0xAA && c.first[1] == 0x11,
              "record payloads correct");
    }

    /* Extended linear address applies a base to following data records. */
    {
        const char *img =
            ":020000040001F9\n"   /* ELA base = 0x0001 << 16 = 0x10000 */
            ":02000000AABB99\n"   /* data at 0x0000 -> absolute 0x10000 */
            ":00000001FF\n";
        struct collected c = {0};
        pakon_result r = pakon_hex_parse_mem(img, strlen(img), collect_cb, &c);
        CHECK(r == PAKON_OK, "ELA buffer parses");
        CHECK(c.n == 1 && c.addr[0] == 0x10000, "ELA base applied to address");
    }

    /* Missing EOF is an error. */
    {
        const char *img = ":02000000AABB99\n";
        struct collected c = {0};
        CHECK(pakon_hex_parse_mem(img, strlen(img), collect_cb, &c)
                  == PAKON_ERR_FIRMWARE,
              "missing EOF rejected");
    }

    if (failures) {
        printf("\n%d test(s) FAILED\n", failures);
        return 1;
    }
    printf("\nall tests passed\n");
    return 0;
}
