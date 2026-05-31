#include "pakon_hex.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

/* Decode one hex nibble, or -1 if not a hex digit. */
static int nibble(int c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* Decode one byte (two nibbles) at *pp, advancing *pp past it. -1 on error. */
static int byte_at(const char **pp)
{
    int hi = nibble((unsigned char)(*pp)[0]);
    if (hi < 0) return -1;
    int lo = nibble((unsigned char)(*pp)[1]);
    if (lo < 0) return -1;
    *pp += 2;
    return (hi << 4) | lo;
}

pakon_result pakon_hex_parse_line(const char *line, pakon_hex_record *out)
{
    if (!line || !out)
        return PAKON_ERR_PARAM;

    const char *p = line;
    while (*p && isspace((unsigned char)*p))
        p++;
    if (*p != ':') {
        pakon_logf(PAKON_LOG_ERROR, "hex: line does not start with ':'");
        return PAKON_ERR_FIRMWARE;
    }
    p++;

    int count = byte_at(&p);
    int ah    = byte_at(&p);
    int al    = byte_at(&p);
    int type  = byte_at(&p);
    if (count < 0 || ah < 0 || al < 0 || type < 0) {
        pakon_logf(PAKON_LOG_ERROR, "hex: truncated/invalid header");
        return PAKON_ERR_FIRMWARE;
    }

    unsigned sum = (unsigned)count + (unsigned)ah + (unsigned)al + (unsigned)type;

    out->addr = (uint32_t)((ah << 8) | al);
    out->type = (uint8_t)type;
    out->len  = (uint8_t)count;

    for (int i = 0; i < count; i++) {
        int b = byte_at(&p);
        if (b < 0) {
            pakon_logf(PAKON_LOG_ERROR, "hex: truncated data (byte %d)", i);
            return PAKON_ERR_FIRMWARE;
        }
        out->data[i] = (uint8_t)b;
        sum += (unsigned)b;
    }

    int cksum = byte_at(&p);
    if (cksum < 0) {
        pakon_logf(PAKON_LOG_ERROR, "hex: missing checksum");
        return PAKON_ERR_FIRMWARE;
    }

    /* Intel HEX checksum: two's complement of the sum of all preceding bytes. */
    if (((sum + (unsigned)cksum) & 0xFFu) != 0) {
        pakon_logf(PAKON_LOG_ERROR, "hex: checksum mismatch (got %02x)", cksum);
        return PAKON_ERR_FIRMWARE;
    }

    /* Trailing whitespace/CR/LF is fine; anything else is junk. */
    while (*p) {
        if (!isspace((unsigned char)*p)) {
            pakon_logf(PAKON_LOG_ERROR, "hex: trailing junk after record");
            return PAKON_ERR_FIRMWARE;
        }
        p++;
    }

    return PAKON_OK;
}

pakon_result pakon_hex_parse_mem(const char *text, size_t len,
                                 pakon_hex_data_cb cb, void *user)
{
    if (!text || !cb)
        return PAKON_ERR_PARAM;

    uint32_t base = 0;        /* ESA/ELA base address */
    int saw_eof = 0;
    char linebuf[1024];

    size_t i = 0;
    while (i < len) {
        /* Collect one line into linebuf (NUL-terminated). */
        size_t j = 0;
        while (i < len && text[i] != '\n') {
            if (j + 1 < sizeof(linebuf))
                linebuf[j++] = text[i];
            i++;
        }
        if (i < len) i++;     /* skip the '\n' */
        linebuf[j] = '\0';

        /* Skip blank / whitespace-only lines. */
        const char *s = linebuf;
        while (*s && isspace((unsigned char)*s))
            s++;
        if (*s == '\0')
            continue;

        pakon_hex_record rec;
        pakon_result r = pakon_hex_parse_line(linebuf, &rec);
        if (r != PAKON_OK)
            return r;

        switch (rec.type) {
        case PAKON_HEX_DATA: {
            uint32_t abs = base + rec.addr;
            r = cb(abs, rec.data, rec.len, user);
            if (r != PAKON_OK)
                return r;
            break;
        }
        case PAKON_HEX_EOF:
            saw_eof = 1;
            break;
        case PAKON_HEX_ESA:
            if (rec.len != 2) return PAKON_ERR_FIRMWARE;
            base = (uint32_t)((rec.data[0] << 8) | rec.data[1]) << 4;
            break;
        case PAKON_HEX_ELA:
            if (rec.len != 2) return PAKON_ERR_FIRMWARE;
            base = (uint32_t)((rec.data[0] << 8) | rec.data[1]) << 16;
            break;
        default:
            pakon_logf(PAKON_LOG_ERROR, "hex: unsupported record type %02x",
                       rec.type);
            return PAKON_ERR_FIRMWARE;
        }

        if (saw_eof)
            break;
    }

    if (!saw_eof) {
        pakon_logf(PAKON_LOG_ERROR, "hex: no EOF record");
        return PAKON_ERR_FIRMWARE;
    }
    return PAKON_OK;
}

pakon_result pakon_hex_parse_file(const char *path,
                                  pakon_hex_data_cb cb, void *user)
{
    if (!path || !cb)
        return PAKON_ERR_PARAM;

    FILE *f = fopen(path, "rb");
    if (!f) {
        pakon_logf(PAKON_LOG_ERROR, "hex: cannot open '%s'", path);
        return PAKON_ERR_FIRMWARE;
    }

    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return PAKON_ERR_FIRMWARE; }
    long sz = ftell(f);
    if (sz < 0) { fclose(f); return PAKON_ERR_FIRMWARE; }
    rewind(f);

    char *buf = (char *)malloc((size_t)sz + 1);
    if (!buf) { fclose(f); return PAKON_ERR_FIRMWARE; }

    size_t got = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    buf[got] = '\0';

    pakon_result r = pakon_hex_parse_mem(buf, got, cb, user);
    free(buf);
    return r;
}
