/*
 * pakon_calib.c — driven CALIBRATE state machine.
 *
 * Pure feedback math + frame builders (unit-tested, no hardware) plus the
 * hardware-driving offset/gain loops. See pakon_calib.h and docs/REGISTERS.md.
 */
#include "pakon_calib.h"
#include "pakon_cmd.h"
#include "pakon_log.h"

#include <string.h>
#include <math.h>

/* ---- pure helpers --------------------------------------------------------- */

pakon_result pakon_calib_build_write(pakon_packet *pkt, uint8_t bank,
                                     uint8_t reg, uint16_t value)
{
    if (!pkt) return PAKON_ERR_PARAM;
    if (bank != PAKON_BANK_TIMING && bank != PAKON_BANK_AFE)
        return PAKON_ERR_PARAM;

    /* data = { addr=0x24, sub=0x03 (write reg), bank, reg, vLo, vHi } */
    const uint8_t data[6] = {
        (uint8_t)AD_PICM, 0x03, bank, reg,
        (uint8_t)(value & 0xff), (uint8_t)((value >> 8) & 0xff)
    };
    return pakon_packet_build(pkt, 0x02, data, sizeof(data));
}

uint16_t pakon_calib_enc_gain(int gain)
{
    if (gain < 0) gain = 0;
    if (gain > (int)PAKON_CALIB_GAIN_MAX) gain = (int)PAKON_CALIB_GAIN_MAX;
    return (uint16_t)gain;
}

uint16_t pakon_calib_enc_offset(int offset)
{
    int mag = offset < 0 ? -offset : offset;
    if (mag > 255) mag = 255;
    uint16_t v = (uint16_t)mag;
    if (offset < 0) v |= 0x100u;   /* bit 8 = negative (sign-magnitude) */
    return v;
}

uint16_t pakon_calib_enc_exposure(int exposure)
{
    if (exposure < 0) exposure = 0;
    if (exposure > (int)PAKON_EXPOSURE_MAX) exposure = (int)PAKON_EXPOSURE_MAX;
    return (uint16_t)exposure;
}

int pakon_calib_converged(unsigned meas, unsigned target,
                          unsigned tol_lo, unsigned tol_hi)
{
    unsigned lo = target > tol_lo ? target - tol_lo : 0u;
    unsigned hi = target + tol_hi;
    return meas >= lo && meas <= hi;
}

int pakon_calib_offset_step(unsigned mean)
{
    /* Proportional step toward the dark target. The OEM expression is
     * (TARGET - mean) * 0x1400 / 0x30000; we use signed arithmetic so a mean
     * above target produces the (correct) negative step. */
    long err = (long)PAKON_CALIB_DARK_TARGET - (long)mean;
    return (int)(err * PAKON_CALIB_OFFSET_NUM / PAKON_CALIB_OFFSET_DEN);
}

double pakon_calib_gain_factor(int gain)
{
    double denom = 1.0 - (double)gain * PAKON_CALIB_GAIN_K;
    if (denom < 1e-6) denom = 1e-6;   /* guard the asymptote near max gain */
    return 1.0 / denom;
}

int pakon_calib_gain_next(double factor, unsigned peak)
{
    if (peak == 0) return (int)PAKON_CALIB_GAIN_MAX;
    double g = factor * (double)PAKON_CALIB_WHITE_TARGET / (double)peak + 0.5;
    if (g < 0.0) g = 0.0;
    if (g > (double)PAKON_CALIB_GAIN_MAX) g = (double)PAKON_CALIB_GAIN_MAX;
    return (int)g;
}

size_t pakon_calib_deinterleave(const uint8_t *raw, size_t raw_len,
                                uint16_t *b, uint16_t *r, uint16_t *g,
                                size_t max_px)
{
    if (!raw) return 0;
    size_t samples = raw_len / 2;
    size_t triples = samples / 3;          /* per-pixel order B, R, G */
    if (triples > max_px) triples = max_px;
    for (size_t i = 0; i < triples; i++) {
        const uint8_t *p = raw + (size_t)6 * i;   /* 3 samples * 2 bytes */
        if (b) b[i] = (uint16_t)(p[0] | (p[1] << 8));
        if (r) r[i] = (uint16_t)(p[2] | (p[3] << 8));
        if (g) g[i] = (uint16_t)(p[4] | (p[5] << 8));
    }
    return triples;
}

unsigned pakon_calib_channel_mean(const uint16_t *ch, size_t col0, size_t col1)
{
    if (!ch || col1 <= col0) return 0;
    uint64_t sum = 0;
    for (size_t i = col0; i < col1; i++) sum += ch[i];
    return (unsigned)(sum / (col1 - col0));
}

unsigned pakon_calib_channel_peak(const uint16_t *ch, size_t col0, size_t col1)
{
    if (!ch || col1 <= col0) return 0;
    unsigned peak = 0;
    for (size_t i = col0; i < col1; i++)
        if (ch[i] > peak) peak = ch[i];
    return peak;
}

/* ---- hardware-driving loops ----------------------------------------------- */

/* Line geometry: 8000 samples / line => 2666 pixels per channel. */
#define CALIB_LINE_SAMPLES   8000u
#define CALIB_LINE_BYTES     (CALIB_LINE_SAMPLES * 2u)   /* 16000 */
#define CALIB_PX_PER_LINE    (CALIB_LINE_SAMPLES / 3u)   /* 2666  */

/* Re-arm kicks (proven from the scan capture; see pakon_replay --scan-sm). */
static const uint8_t CALIB_HOST_ARM[] = {0x02,0x04,0x10,0x01,0x84,0x02};
static const uint8_t CALIB_PICL_ARM[] = {0x04,0x03,0x20,0x00,0x8a};

/* Write one CCD register and verify the command was accepted. */
static pakon_result calib_write_reg(pakon_dev *dev, uint8_t bank, uint8_t reg,
                                    uint16_t value, unsigned timeout_ms)
{
    pakon_packet pkt, reply;
    pakon_result rc = pakon_calib_build_write(&pkt, bank, reg, value);
    if (rc != PAKON_OK) return rc;
    return pakon_cmd(dev, &pkt, &reply, timeout_ms);
}

/*
 * Acquire `nlines` worth of open-gate CCD samples into `buf` (capacity
 * `buf_cap` bytes). Kicks the readout (host arm + PICL 0x8a) then reads 0x86
 * until enough bytes arrive or a read window comes back empty. Returns the byte
 * count gathered.
 *
 * NEEDS-HARDWARE: this is the calibration (no-motor) acquisition seam. The kicks
 * are the ones the preview/calibration phase uses in the capture; whether a
 * single arm yields one readable line or the readout needs repeated arming is
 * to be confirmed on the Linux box. Structured so only this function changes.
 */
static size_t calib_acquire(pakon_dev *dev, size_t nlines,
                            uint8_t *buf, size_t buf_cap, unsigned timeout_ms)
{
    size_t want = nlines * CALIB_LINE_BYTES;
    if (want > buf_cap) want = buf_cap;
    size_t total = 0;
    pakon_packet reply;

    for (unsigned tries = 0; tries < nlines + 4 && total < want; tries++) {
        /* arm the readout */
        pakon_cmd_raw(dev, CALIB_HOST_ARM[0], CALIB_HOST_ARM + 2,
                      CALIB_HOST_ARM[1], &reply, timeout_ms);
        pakon_cmd_raw(dev, CALIB_PICL_ARM[0], CALIB_PICL_ARM + 2,
                      CALIB_PICL_ARM[1], &reply, timeout_ms);

        size_t got = 0;
        pakon_result r = pakon_usb_recv(dev, PAKON_EP_IMAGE_IN,
                                        buf + total, want - total, &got,
                                        timeout_ms);
        total += got;
        if (got == 0 || (r != PAKON_OK && r != PAKON_ERR_TIMEOUT))
            break;
    }
    return total;
}

/* Measure per-channel mean+peak over the column window from `buf`. */
static void calib_measure(const uint8_t *buf, size_t len,
                          size_t col0, size_t col1,
                          unsigned mean[3], unsigned peak[3])
{
    static uint16_t b[CALIB_PX_PER_LINE], r[CALIB_PX_PER_LINE], g[CALIB_PX_PER_LINE];
    size_t lines = len / CALIB_LINE_BYTES;
    if (lines == 0) lines = (len >= 6) ? 1 : 0;   /* at least a partial line */

    uint64_t sm[3] = {0,0,0};
    unsigned pk[3] = {0,0,0};
    size_t nmean = 0;

    for (size_t ln = 0; ln < lines; ln++) {
        const uint8_t *line = buf + ln * CALIB_LINE_BYTES;
        size_t avail = len - ln * CALIB_LINE_BYTES;
        if (avail > CALIB_LINE_BYTES) avail = CALIB_LINE_BYTES;
        size_t px = pakon_calib_deinterleave(line, avail, b, r, g,
                                             CALIB_PX_PER_LINE);
        size_t c1 = col1 < px ? col1 : px;
        if (c1 <= col0) continue;
        const uint16_t *ch[3] = {r, g, b};
        for (int k = 0; k < 3; k++) {
            sm[k] += pakon_calib_channel_mean(ch[k], col0, c1);
            unsigned p = pakon_calib_channel_peak(ch[k], col0, c1);
            if (p > pk[k]) pk[k] = p;
        }
        nmean++;
    }
    for (int k = 0; k < 3; k++) {
        mean[k] = nmean ? (unsigned)(sm[k] / nmean) : 0u;
        peak[k] = pk[k];
    }
}

pakon_result pakon_calib_run(pakon_dev *dev, const pakon_calib_opts *opts,
                             pakon_calib_result *result)
{
    if (!dev || !result) return PAKON_ERR_PARAM;

    pakon_calib_opts o = {0};
    if (opts) o = *opts;
    if (o.nlines == 0)     o.nlines = 32;
    if (o.timeout_ms == 0) o.timeout_ms = 2000;
    if (o.exposure == 0)   o.exposure = 256;   /* nominal gain-phase integration */
    if (o.col1 == 0)       { o.col0 = 100; o.col1 = 1900; }  /* visible window */
    if (!opts)             { o.do_dark = 1; o.do_gain = 1; }

    memset(result, 0, sizeof(*result));

    /* Bounded acquisition buffer (cap at 64 lines = ~1 MB). */
    size_t nlines = o.nlines > 64 ? 64 : o.nlines;
    static uint8_t buf[64 * CALIB_LINE_BYTES];
    const uint8_t bank_off_reg[3] = {PAKON_REG_OFFSET_R, PAKON_REG_OFFSET_G, PAKON_REG_OFFSET_B};
    const uint8_t bank_gain_reg[3] = {PAKON_REG_GAIN_R, PAKON_REG_GAIN_G, PAKON_REG_GAIN_B};

    /* ---- Phase 1: dark offset (gain 0) ---- */
    if (o.do_dark) {
        for (int k = 0; k < 3; k++) {
            calib_write_reg(dev, PAKON_BANK_AFE, bank_gain_reg[k],
                            pakon_calib_enc_gain(0), o.timeout_ms);
            result->offset[k] = PAKON_CALIB_OFFSET_INIT;
        }
        int conv[3] = {0,0,0};
        unsigned mean[3] = {0,0,0}, peak[3] = {0,0,0};
        for (unsigned it = 0; it < PAKON_CALIB_DARK_ITERS; it++) {
            for (int k = 0; k < 3; k++)
                calib_write_reg(dev, PAKON_BANK_AFE, bank_off_reg[k],
                                pakon_calib_enc_offset(result->offset[k]),
                                o.timeout_ms);
            size_t got = calib_acquire(dev, nlines, buf, sizeof(buf), o.timeout_ms);
            calib_measure(buf, got, o.col0, o.col1, mean, peak);
            int all = 1;
            for (int k = 0; k < 3; k++) {
                if (pakon_calib_converged(mean[k], PAKON_CALIB_DARK_TARGET,
                                          PAKON_CALIB_DARK_TOL, PAKON_CALIB_DARK_TOL))
                    conv[k] = 1;
                else all = 0;
                result->offset[k] += pakon_calib_offset_step(mean[k]);
                result->dark[k] = mean[k];
            }
            if (o.verbose)
                pakon_logf(PAKON_LOG_INFO,
                    "calib dark it=%u mean=%u/%u/%u off=%d/%d/%d (%zuB)",
                    it, mean[0], mean[1], mean[2],
                    result->offset[0], result->offset[1], result->offset[2], got);
            if (all) break;
        }
        result->offset_converged = conv[0] && conv[1] && conv[2];
    }

    /* ---- Phase 2: gain (illuminated open gate) ---- */
    if (o.do_gain) {
        int gain[3] = {0,0,0};
        double factor[3] = {1.0, 1.0, 1.0};
        unsigned mean[3] = {0,0,0}, peak[3] = {0,0,0};
        int conv[3] = {0,0,0};
        /* Program a nominal exposure on all channels so the CCD integrates real
         * light (the OEM sets this in its gain loop; without it the sensor reads
         * dark). bank 0x82 regs 1/2/3 = CcdExposure R/G/B. */
        const uint8_t exp_reg[3] = {PAKON_REG_EXPOSURE_R, PAKON_REG_EXPOSURE_G, PAKON_REG_EXPOSURE_B};
        for (int k = 0; k < 3; k++)
            calib_write_reg(dev, PAKON_BANK_TIMING, exp_reg[k],
                            pakon_calib_enc_exposure((int)o.exposure), o.timeout_ms);
        for (unsigned it = 0; it < PAKON_CALIB_GAIN_ITERS; it++) {
            for (int k = 0; k < 3; k++)
                calib_write_reg(dev, PAKON_BANK_AFE, bank_gain_reg[k],
                                pakon_calib_enc_gain(gain[k]), o.timeout_ms);
            size_t got = calib_acquire(dev, nlines, buf, sizeof(buf), o.timeout_ms);
            calib_measure(buf, got, o.col0, o.col1, mean, peak);
            int all = 1;
            for (int k = 0; k < 3; k++) {
                if (pakon_calib_converged(peak[k], PAKON_CALIB_WHITE_TARGET,
                                          PAKON_CALIB_WHITE_TOL_LO, PAKON_CALIB_WHITE_TOL_HI))
                    conv[k] = 1;
                else all = 0;
                result->white[k] = peak[k];
                result->gain[k] = gain[k];
            }
            if (o.verbose)
                pakon_logf(PAKON_LOG_INFO,
                    "calib gain it=%u peak=%u/%u/%u gain=%d/%d/%d (%zuB)",
                    it, peak[0], peak[1], peak[2], gain[0], gain[1], gain[2], got);
            if (all) break;
            for (int k = 0; k < 3; k++) {
                gain[k] = pakon_calib_gain_next(factor[k], peak[k]);
                factor[k] = pakon_calib_gain_factor(gain[k]);
            }
        }
        result->gain_converged = conv[0] && conv[1] && conv[2];
    }

    return PAKON_OK;
}
