#include "pakon_log.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>

const char *pakon_result_str(pakon_result r)
{
    switch (r) {
    case PAKON_OK:              return "OK";
    case PAKON_ERR_PARAM:       return "invalid argument";
    case PAKON_ERR_NO_DEVICE:   return "no device";
    case PAKON_ERR_USB:         return "USB error";
    case PAKON_ERR_TIMEOUT:     return "timeout";
    case PAKON_ERR_PROTO:       return "protocol error";
    case PAKON_ERR_STATUS:      return "device error status";
    case PAKON_ERR_FIRMWARE:    return "firmware error";
    case PAKON_ERR_UNIMPLEMENTED: return "unimplemented";
    }
    return "unknown";
}

/* Resolve PAKON_DEBUG once and cache it. */
static pakon_log_level current_level(void)
{
    static int cached = -1;
    if (cached < 0) {
        const char *env = getenv("PAKON_DEBUG");
        long v = env ? strtol(env, NULL, 10) : 0;
        if (v < PAKON_LOG_ERROR) v = PAKON_LOG_ERROR;
        if (v > PAKON_LOG_TRACE) v = PAKON_LOG_TRACE;
        cached = (int)v;
    }
    return (pakon_log_level)cached;
}

int pakon_log_enabled(pakon_log_level level)
{
    return level <= current_level();
}

static const char *level_tag(pakon_log_level level)
{
    switch (level) {
    case PAKON_LOG_ERROR: return "ERROR";
    case PAKON_LOG_WARN:  return "WARN ";
    case PAKON_LOG_INFO:  return "INFO ";
    case PAKON_LOG_DEBUG: return "DEBUG";
    case PAKON_LOG_TRACE: return "TRACE";
    }
    return "?????";
}

void pakon_logf(pakon_log_level level, const char *fmt, ...)
{
    if (!pakon_log_enabled(level))
        return;

    va_list ap;
    fprintf(stderr, "[pakon %s] ", level_tag(level));
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
}

void pakon_hexdump(pakon_log_level level, const char *tag,
                   const void *buf, size_t len)
{
    if (!pakon_log_enabled(level))
        return;

    const unsigned char *p = (const unsigned char *)buf;
    fprintf(stderr, "[pakon %s] %s (%zu bytes)\n",
            level_tag(level), tag ? tag : "", len);

    for (size_t off = 0; off < len; off += 16) {
        fprintf(stderr, "  %04zx  ", off);
        for (size_t i = 0; i < 16; i++) {
            if (off + i < len)
                fprintf(stderr, "%02x ", p[off + i]);
            else
                fprintf(stderr, "   ");
            if (i == 7)
                fputc(' ', stderr);
        }
        fputc(' ', stderr);
        for (size_t i = 0; i < 16 && off + i < len; i++) {
            unsigned char c = p[off + i];
            fputc((c >= 0x20 && c < 0x7f) ? (int)c : '.', stderr);
        }
        fputc('\n', stderr);
    }
}
