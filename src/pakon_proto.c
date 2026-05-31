/*
 * pakon_proto.c — packet framing layer (STUBS for Phase 0).
 *
 * Only the pure name-lookup helpers are real here. Checksum, build, and parse
 * are intentionally unimplemented: the checksum algorithm must first be
 * derived and validated against real sample packets in Phase 3 before any of
 * these can be trusted (see "Standing risks" in PAKON_SANE_PLAN.md).
 */
#include "pakon_proto.h"

const char *pakon_status_str(pakon_status s)
{
    switch (s) {
    case PS_SUCCESS:      return "success";
    case PS_NOT_ACKED:    return "not acked";
    case PS_INVALID_PKT:  return "invalid packet";
    case PS_BAD_CHECKSUM: return "bad checksum";
    case PS_USB_4:        return "usb error (4)";
    case PS_USB_5:        return "usb error (5)";
    case PS_USB_6:        return "usb error (6)";
    case PS_HOST_ALGO:    return "host algorithm error";
    case PS_SUCCESS_8:    return "success (8)";
    case PS_BUS_ERROR:    return "bus error";
    }
    return "unknown status";
}

const char *pakon_addr_str(pakon_addr a)
{
    switch (a) {
    case AD_HOST:           return "AD_HOST";
    case AD_PICL:           return "AD_PICL";
    case AD_BOOT_PICL:      return "AD_BOOT_PICL";
    case AD_PICM:           return "AD_PICM";
    case AD_BOOT_PICM:      return "AD_BOOT_PICM";
    case AD_PICL_PLUS:      return "AD_PICL_PLUS";
    case AD_BOOT_PICL_PLUS: return "AD_BOOT_PICL_PLUS";
    case AD_PICM_PLUS:      return "AD_PICM_PLUS";
    case AD_BOOT_PICM_PLUS: return "AD_BOOT_PICM_PLUS";
    }
    return "AD_?";
}

pakon_result pakon_checksum(const pakon_packet *pkt, uint8_t *out_sum)
{
    /* TODO(Phase 3): derive from known-good packets, e.g. 04 03 10 00 85. */
    (void)pkt;
    if (out_sum)
        *out_sum = 0;
    return PAKON_ERR_UNIMPLEMENTED;
}

pakon_result pakon_packet_build(pakon_packet *pkt, uint8_t type,
                                const uint8_t *data, size_t dlen)
{
    /* TODO(Phase 3). */
    (void)pkt; (void)type; (void)data; (void)dlen;
    return PAKON_ERR_UNIMPLEMENTED;
}

pakon_result pakon_packet_parse(pakon_packet *pkt, const uint8_t *raw,
                                size_t rawlen)
{
    /* TODO(Phase 3). */
    (void)pkt; (void)raw; (void)rawlen;
    return PAKON_ERR_UNIMPLEMENTED;
}
