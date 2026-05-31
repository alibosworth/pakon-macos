#include "pakon_cmd.h"

#include <string.h>

pakon_result pakon_cmd(pakon_dev *dev, const pakon_packet *cmd,
                       pakon_packet *reply, unsigned timeout_ms)
{
    if (!dev || !cmd)
        return PAKON_ERR_PARAM;

    uint8_t wire[PAKON_PACKET_SIZE];
    size_t wlen = 0;
    pakon_result r = pakon_packet_serialize(cmd, wire, sizeof(wire), &wlen);
    if (r != PAKON_OK)
        return r;

    size_t sent = 0;
    r = pakon_usb_send(dev, PAKON_EP_CMD_OUT, wire, wlen, &sent, timeout_ms);
    if (r != PAKON_OK)
        return r;
    if (sent != wlen)
        return PAKON_ERR_USB;

    if (!reply)
        return PAKON_OK;

    /* Replies are short frames; a max-struct-sized buffer is plenty. */
    uint8_t buf[PAKON_PACKET_SIZE];
    size_t got = 0;
    r = pakon_usb_recv(dev, PAKON_EP_CMD_IN, buf, sizeof(buf), &got, timeout_ms);
    if (r != PAKON_OK)
        return r;

    return pakon_packet_parse(reply, buf, got);
}

pakon_result pakon_cmd_raw(pakon_dev *dev, uint8_t type,
                           const uint8_t *data, size_t dlen,
                           pakon_packet *reply, unsigned timeout_ms)
{
    pakon_packet cmd;
    pakon_result r = pakon_packet_build(&cmd, type, data, dlen);
    if (r != PAKON_OK)
        return r;
    return pakon_cmd(dev, &cmd, reply, timeout_ms);
}
