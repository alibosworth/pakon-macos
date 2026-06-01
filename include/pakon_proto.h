/*
 * pakon_proto.h — the Pakon packet (framing) layer.
 *
 * Pure byte-level encode/decode. NO transport (libusb) appears here, by
 * design (the protocol layer never reaches down into the transport). Only
 * the documented 36-byte command frame and its enums live at this layer.
 *
 * Documented frame (from the reverse-engineering notes):
 *   byte 0      : type   (see pakon_ptype)
 *   byte 1      : count  (length of `data`, max 34)
 *   bytes 2..35 : data   (34 bytes); data[0] is an address byte (pakon_addr)
 *   total       : 36 bytes
 */
#ifndef PAKON_PROTO_H
#define PAKON_PROTO_H

#include <stdint.h>
#include <stddef.h>

#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
#include <assert.h>
#define PAKON_STATIC_ASSERT(cond, msg) _Static_assert(cond, msg)
#else
#define PAKON_STATIC_ASSERT(cond, msg) \
    typedef char pakon_static_assert_##__LINE__[(cond) ? 1 : -1]
#endif

#include "pakon_log.h"   /* for pakon_result */

#ifdef __cplusplus
extern "C" {
#endif

#define PAKON_PACKET_SIZE   36u
#define PAKON_DATA_MAX      34u   /* bytes 2..35 */

/*
 * Address byte (data[0]). These values are documented in the background notes
 * and are treated as known-good.
 */
typedef enum {
    AD_HOST           = 0x10,
    AD_PICL           = 0x20,
    AD_BOOT_PICL      = 0x22,
    AD_PICM           = 0x24,
    AD_BOOT_PICM      = 0x26,
    AD_PICL_PLUS      = 0x40,
    AD_BOOT_PICL_PLUS = 0x42,
    AD_PICM_PLUS      = 0x44,
    AD_BOOT_PICM_PLUS = 0x46
} pakon_addr;

/*
 * Scanner status code (4th byte of a scanner->host packet, i.e. data[1] when
 * data[0] is the echoed address). Documented values, treated as known-good.
 */
typedef enum {
    PS_SUCCESS      = 0,
    PS_NOT_ACKED    = 1,
    PS_INVALID_PKT  = 2,
    PS_BAD_CHECKSUM = 3,
    PS_USB_4        = 4,   /* 4..6 are USB-related; exact meanings TBD */
    PS_USB_5        = 5,
    PS_USB_6        = 6,
    PS_HOST_ALGO    = 7,
    PS_SUCCESS_8    = 8,   /* also reported as success in some sequences */
    PS_BUS_ERROR    = 9
} pakon_status;

/*
 * Packet type (byte 0). NOTE: the *names* PH_CMD / PH_READ_STATUS / PH_INVALID
 * come from the documentation, but their numeric wire values are NOT yet
 * confirmed from captures. We have *observed* 0x04 on host->device command
 * frames and 0x07 on the device->host reply in the open handshake, but we have
 * not proven those map to these names. Until Phase 3 nails this down from real
 * traffic, the type byte is carried as a raw uint8_t in pakon_packet and these
 * constants are placeholders — do not rely on the values.
 */
typedef enum {
    PH_CMD         = 0,   /* placeholder value — confirm in Phase 3 */
    PH_READ_STATUS = 1,   /* placeholder value — confirm in Phase 3 */
    PH_INVALID     = 2    /* placeholder value — confirm in Phase 3 */
} pakon_ptype;

/*
 * The wire frame, laid out exactly. Packed so sizeof is guaranteed 36 with no
 * padding regardless of compiler/ABI.
 */
#if defined(__GNUC__) || defined(__clang__)
typedef struct __attribute__((packed)) {
    uint8_t type;                    /* byte 0  */
    uint8_t count;                   /* byte 1  */
    uint8_t data[PAKON_DATA_MAX];    /* bytes 2..35; data[0] is the address */
} pakon_packet;
#else
#pragma pack(push, 1)
typedef struct {
    uint8_t type;
    uint8_t count;
    uint8_t data[PAKON_DATA_MAX];
} pakon_packet;
#pragma pack(pop)
#endif

PAKON_STATIC_ASSERT(sizeof(pakon_packet) == PAKON_PACKET_SIZE,
                    "pakon_packet must be exactly 36 bytes");

/* Human-readable names (never NULL). */
const char *pakon_status_str(pakon_status s);
const char *pakon_addr_str(pakon_addr a);

/*
 * On-wire length of a frame. CONFIRMED from capture: a frame is transmitted as
 * exactly `2 + count` bytes ([type][count][count data bytes]) — it is NOT
 * padded to PAKON_PACKET_SIZE (36 is only the max in-memory struct size).
 */
static inline size_t pakon_wire_len(const pakon_packet *pkt)
{
    return (size_t)2 + pkt->count;
}

/*
 * Build a frame into `pkt`: sets type, count=`dlen`, and copies `data` (which
 * includes the address byte as data[0] and any command/parameter bytes). Note
 * we do NOT compute a checksum here: captured short frames carry no separate
 * checksum byte (e.g. `04 03 44 00 00` ends in 00 while `04 03 10 00 85` ends
 * in 85 — the trailing byte is a command/parameter, not a checksum). If a
 * checksum scheme is later found for longer frames it will be added explicitly.
 * Returns PAKON_ERR_PARAM if dlen > PAKON_DATA_MAX.
 */
pakon_result pakon_packet_build(pakon_packet *pkt, uint8_t type,
                                const uint8_t *data, size_t dlen);

/*
 * Serialize `pkt` into `out` (must hold at least pakon_wire_len(pkt) bytes).
 * Writes `*out_len` = the wire length actually written.
 */
pakon_result pakon_packet_serialize(const pakon_packet *pkt,
                                    uint8_t *out, size_t out_cap,
                                    size_t *out_len);

/*
 * Parse `rawlen` received bytes into `pkt`, validating self-consistency
 * (rawlen >= 2 and rawlen == 2 + raw[1], count <= PAKON_DATA_MAX).
 */
pakon_result pakon_packet_parse(pakon_packet *pkt, const uint8_t *raw,
                                size_t rawlen);

/* Convenience accessors (valid once count is set). */
static inline uint8_t pakon_packet_addr(const pakon_packet *pkt)
{
    return pkt->count >= 1 ? pkt->data[0] : 0;
}
/* In a device->host reply the status byte follows the address (data[1]). */
static inline uint8_t pakon_packet_status(const pakon_packet *pkt)
{
    return pkt->count >= 2 ? pkt->data[1] : 0;
}

#ifdef __cplusplus
}
#endif

#endif /* PAKON_PROTO_H */
