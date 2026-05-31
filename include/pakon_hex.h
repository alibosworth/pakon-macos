/*
 * pakon_hex.h — Intel HEX parser (transport-internal, hardware-free).
 *
 * The FX2 firmware ships as Intel HEX. Parsing it is a documented standard, so
 * it is implemented and unit-tested with no hardware (Phase 1). The bytes it
 * yields are fed to the FX2 RAM-download routine in pakon_usb.c.
 *
 * No libusb and no Pakon packet knowledge here — this is pure text→bytes.
 */
#ifndef PAKON_HEX_H
#define PAKON_HEX_H

#include <stdint.h>
#include <stddef.h>

#include "pakon_log.h"   /* for pakon_result */

#ifdef __cplusplus
extern "C" {
#endif

/* Intel HEX record types we recognize. */
enum {
    PAKON_HEX_DATA = 0x00,
    PAKON_HEX_EOF  = 0x01,
    PAKON_HEX_ESA  = 0x02,   /* extended segment address */
    PAKON_HEX_ELA  = 0x04    /* extended linear address */
};

/* One decoded record. `addr` is the raw 16-bit record address (no base
 * applied); the parser applies ESA/ELA base addressing itself when iterating. */
typedef struct {
    uint32_t addr;            /* 16-bit address field of this record */
    uint8_t  type;            /* record type (see enum above) */
    uint8_t  len;             /* number of data bytes */
    uint8_t  data[255];       /* up to 255 data bytes */
} pakon_hex_record;

/*
 * Parse and validate a single ':'-prefixed HEX line into `out`. Validates the
 * record-length field, the trailing checksum, and field bounds. Returns
 * PAKON_OK or PAKON_ERR_FIRMWARE. Leading/trailing whitespace and a trailing
 * CR/LF are tolerated.
 */
pakon_result pakon_hex_parse_line(const char *line, pakon_hex_record *out);

/* Callback invoked for each DATA record, with the absolute address (ESA/ELA
 * base already applied). Return non-OK to abort the parse with that result. */
typedef pakon_result (*pakon_hex_data_cb)(uint32_t addr, const uint8_t *data,
                                          uint8_t len, void *user);

/*
 * Iterate every DATA record in a HEX text buffer, applying segment/linear base
 * addressing and stopping at the EOF record. Blank/whitespace-only lines are
 * skipped. Returns PAKON_OK once EOF is reached, PAKON_ERR_FIRMWARE on any
 * malformed line or a missing EOF, or whatever the callback returned on abort.
 */
pakon_result pakon_hex_parse_mem(const char *text, size_t len,
                                 pakon_hex_data_cb cb, void *user);

/* Read `path` into memory and run pakon_hex_parse_mem on it. */
pakon_result pakon_hex_parse_file(const char *path,
                                  pakon_hex_data_cb cb, void *user);

#ifdef __cplusplus
}
#endif

#endif /* PAKON_HEX_H */
