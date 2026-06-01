/*
 * pakon_calib.h — driven CALIBRATE state machine (milestone 3 of the
 * capture-free backend).
 *
 * Replaces verbatim capture-replay of frozen gain/offset/exposure values with a
 * DRIVEN calibration that MEASURES the open-gate CCD output and COMPUTES the
 * register values each session, so the same negative scans the same way despite
 * lamp/CCD drift. The algorithm is reversed from the OEM software (TLA.c
 * FUN_10022f80); see docs/REGISTERS.md "The CALIBRATE feedback algorithm".
 *
 * Layering: like pakon_cmd, this is glue that uses BOTH lower layers — it builds
 * protocol frames (pakon_proto) and moves them over the transport (pakon_usb,
 * via pakon_cmd). It contains NO SANE knowledge. The pure feedback math and the
 * frame builders are split out as standalone functions so they can be unit
 * tested without hardware (see test/test_calib.c).
 *
 * CONFIRMED facts this module depends on (docs/REGISTERS.md, capture-mined):
 *   - CCD register writes target wire address 0x24 (AD_PICM), frame
 *       02 06 24 03 <bank> <reg> <vLo> <vHi>   (16-bit LE value).
 *   - bank 0x84 (AFE): reg 2/3/4 = Gain R/G/B (6-bit); reg 5/6/7 = Offset R/G/B
 *     (sign-magnitude, |v|<=255, bit 0x100 = negative).
 *   - bank 0x82 (timing): reg 1/2/3 = CcdExposure R/G/B (12-bit); reg 6 = Height.
 *   - image stream is 16-bit LE, per-pixel interleaved in order B,R,G.
 */
#ifndef PAKON_CALIB_H
#define PAKON_CALIB_H

#include <stdint.h>
#include <stddef.h>

#include "pakon_usb.h"
#include "pakon_proto.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---- register map (banks/regs within the 0x24 write frame) ---------------- */

#define PAKON_BANK_TIMING   0x82u   /* exposure / height / control */
#define PAKON_BANK_AFE      0x84u   /* gain / offset */

#define PAKON_REG_GAIN_R    2u      /* bank 0x84 */
#define PAKON_REG_GAIN_G    3u
#define PAKON_REG_GAIN_B    4u
#define PAKON_REG_OFFSET_R  5u
#define PAKON_REG_OFFSET_G  6u
#define PAKON_REG_OFFSET_B  7u

#define PAKON_REG_EXPOSURE_R 1u     /* bank 0x82 */
#define PAKON_REG_EXPOSURE_G 2u
#define PAKON_REG_EXPOSURE_B 3u
#define PAKON_REG_HEIGHT     6u

/* ---- algorithm constants (from FUN_10022f80; see docs/REGISTERS.md) -------- */

#define PAKON_CALIB_DARK_TARGET   300u    /* dark-offset per-channel mean target */
#define PAKON_CALIB_DARK_TOL      0x20u   /* +/- tolerance (FUN_100215a0)        */
#define PAKON_CALIB_DARK_ITERS    8u      /* max offset iterations               */
#define PAKON_CALIB_OFFSET_INIT   100     /* initial offset guess (per channel)  */
#define PAKON_CALIB_OFFSET_NUM    0x1400  /* proportional step numerator         */
#define PAKON_CALIB_OFFSET_DEN    0x30000 /* proportional step denominator       */

#define PAKON_CALIB_WHITE_TARGET  64000u  /* gain per-channel peak target        */
#define PAKON_CALIB_WHITE_TOL_LO  0u      /* lower tolerance (asymmetric)        */
#define PAKON_CALIB_WHITE_TOL_HI  0x800u  /* upper tolerance                     */
#define PAKON_CALIB_GAIN_ITERS    4u      /* max gain iterations                 */
#define PAKON_CALIB_GAIN_MAX      0x3fu   /* 6-bit gain register clamp           */

/*
 * Gain linearization constant k in factor = 1/(1 - gain*k) (OEM FUN_100227e0).
 * The exact .data value is not recoverable from the decompile; k ~= 1/64 keeps
 * the factor finite across the 6-bit gain range. TUNABLE — confirm on hardware
 * by checking the gain loop converges to the OEM seed (~13). See docs/REGISTERS.md.
 */
#define PAKON_CALIB_GAIN_K        (1.0 / 64.0)

#define PAKON_EXPOSURE_MAX        0xfffu  /* 12-bit exposure clamp */
#define PAKON_EXPOSURE_MIN        0x0du   /* OEM minimum (post-regression clamp) */

/*
 * Stream-prime before the gain phase: a few discarded acquisitions to get the
 * readout flowing after the exposure/control writes (the open-gate level is
 * stable immediately on this hardware — there is no slow lamp ramp — so this is
 * just priming, not a warm-up wait). Exits early if a channel peak crosses
 * WARMUP_BRIGHT.
 */
#define PAKON_CALIB_WARMUP_BRIGHT 30000u  /* early-exit level */
#define PAKON_CALIB_WARMUP_MAX    2u      /* priming rounds (× nlines) */

/* ---- pure helpers (no hardware; unit-tested) ------------------------------ */

/*
 * Build a single-register CCD write frame into `pkt`:
 *   type=0x02, count=6, data = { 0x24, 0x03, bank, reg, vLo, vHi }
 * (addr AD_PICM, sub-command 0x03 = write reg, 16-bit LE value). `bank` must be
 * PAKON_BANK_TIMING or PAKON_BANK_AFE. Returns PAKON_ERR_PARAM otherwise.
 */
pakon_result pakon_calib_build_write(pakon_packet *pkt, uint8_t bank,
                                     uint8_t reg, uint16_t value);

/* Encode a gain value to the 6-bit register field (clamp 0..63). */
uint16_t pakon_calib_enc_gain(int gain);

/*
 * Encode a signed offset to the AFE sign-magnitude field: magnitude clamped to
 * 0..255, bit 0x100 set when negative.
 */
uint16_t pakon_calib_enc_offset(int offset);

/* Clamp an exposure to the 12-bit register range [0, 0xfff]. */
uint16_t pakon_calib_enc_exposure(int exposure);

/*
 * Convergence predicate, matching OEM FUN_100215a0:
 *   returns 1 iff (target - tol_lo) <= meas <= (target + tol_hi).
 */
int pakon_calib_converged(unsigned meas, unsigned target,
                          unsigned tol_lo, unsigned tol_hi);

/*
 * Proportional dark-offset step: returns (DARK_TARGET - mean) * NUM / DEN
 * (integer, signed). Added to the current offset each iteration.
 */
int pakon_calib_offset_step(unsigned mean);

/* Gain linearization factor for an applied gain: 1/(1 - gain*k). */
double pakon_calib_gain_factor(int gain);

/*
 * Next gain estimate from the running factor and the measured peak:
 *   round(factor * WHITE_TARGET / peak), clamped to [0, GAIN_MAX].
 * `peak` of 0 yields GAIN_MAX (treat a black read as "need maximum gain").
 */
int pakon_calib_gain_next(double factor, unsigned peak);

/*
 * Deinterleave a raw scan-line buffer (16-bit LE samples, per-pixel order
 * B,R,G) into three channel arrays. Writes up to `max_px` pixels per channel;
 * returns the pixel count written. `raw_len` is in bytes. Channel pointers may
 * be NULL to skip a channel.
 */
size_t pakon_calib_deinterleave(const uint8_t *raw, size_t raw_len,
                                uint16_t *b, uint16_t *r, uint16_t *g,
                                size_t max_px);

/* Mean of `ch[col0 .. col1)` (col1 exclusive). Returns 0 if the range is empty. */
unsigned pakon_calib_channel_mean(const uint16_t *ch, size_t col0, size_t col1);

/* Peak (max) of `ch[col0 .. col1)`. Returns 0 if the range is empty. */
unsigned pakon_calib_channel_peak(const uint16_t *ch, size_t col0, size_t col1);

/* ---- synthesized CONFIGURE (capture-free register programming) ------------ */

/*
 * The full CCD calibration register set, written to wire addr 0x24 (PICM). The
 * defaults (pakon_calib_default_config) are the OEM's FINAL converged values from
 * the reference capture — validated, not frozen-from-replay: we emit them as
 * named registers from C, so a scan no longer depends on a captured .pakscan.
 * gain/offset are the live-calibratable fields; the timing/AFE constants are the
 * fixed sensor setup. NOTE CcdExposure (0x82.1/2/3) is 0 in the OEM final state —
 * integration is governed by the timing regs (4/5/9/0xa), not exposure.
 */
typedef struct {
    uint16_t control;      /* 0x82.0  control bitmask        (OEM 0x0160) */
    uint16_t exposure[3];  /* 0x82.1/2/3 CcdExposure R/G/B    (OEM 0)      */
    uint16_t timing4;      /* 0x82.4                          (OEM 0x002b) */
    uint16_t timing5;      /* 0x82.5                          (OEM 0x07fb) */
    uint16_t height;       /* 0x82.6  Height/integration      (OEM 0x0c1a) */
    uint16_t timing9;      /* 0x82.9                          (OEM 0x001f) */
    uint16_t timing10;     /* 0x82.0a                         (OEM 0x0400) */
    uint16_t afe0;         /* 0x84.0  AFE config              (OEM 0x0078) */
    uint16_t afe1;         /* 0x84.1  AFE config              (OEM 0x0080) */
    int      gain[3];      /* 0x84.2/3/4 Gain R/G/B           (OEM 13)     */
    int      offset[3];    /* 0x84.5/6/7 Offset R/G/B (signed)(OEM -38/-31/-31) */
} pakon_calib_config;

/* The OEM's validated converged calibration (from the reference capture). */
pakon_calib_config pakon_calib_default_config(void);

/*
 * Program the full calibration register set onto the device (wire addr 0x24),
 * timing bank first then AFE. Assumes OPEN + PIC/CCD init already done (the fixed
 * boilerplate, e.g. via the captured init prelude). Returns the first non-OK
 * result, or PAKON_OK if every register write was accepted.
 */
pakon_result pakon_calib_configure(pakon_dev *dev, const pakon_calib_config *cfg,
                                   unsigned timeout_ms);

/* ---- cached-calibration (EEPROM) drift detection -------------------------- */

/*
 * Baseline checksums of the param table read from the reference unit (the u32 at
 * offset 4 of each framed region; see pakon_replay --read-params). If a freshly
 * read table matches these, the default_config above is valid for this unit; a
 * mismatch means the scanner recalibrated or it is a different unit.
 */
#define PAKON_CALIB_EEPROM_CKSUM_R1  0x52ffea66u
#define PAKON_CALIB_EEPROM_CKSUM_R2  0x873e6ed3u

/* ---- driven calibration (hardware) ---------------------------------------- */

/* Per-channel result of a calibration pass. */
typedef struct {
    int gain[3];        /* R, G, B applied gain register values   */
    int offset[3];      /* R, G, B applied offset (signed)        */
    int exposure[3];    /* R, G, B exposure (0 if not calibrated) */
    unsigned dark[3];   /* final measured dark mean per channel   */
    unsigned white[3];  /* final measured white peak per channel  */
    int offset_converged;
    int gain_converged;
} pakon_calib_result;

/* Tunable knobs for a run (0 = use defaults). */
typedef struct {
    size_t   nlines;        /* CCD lines to accumulate per measurement (def 32) */
    size_t   col0, col1;    /* measurement column window (def: central 3..N)    */
    unsigned timeout_ms;    /* per-transfer timeout (def 2000)                  */
    unsigned exposure;      /* nominal CcdExposure for the gain phase (def 256) */
    int      do_dark;       /* run the dark-offset phase (def 1)                */
    int      do_gain;       /* run the gain phase (def 1)                       */
    int      verbose;       /* log each iteration                               */
} pakon_calib_opts;

/*
 * Run the driven calibration on an opened+claimed device. Assumes the caller
 * has already brought the device to a state where the open gate (no film) is
 * presented and the CCD can be read (OPEN + PIC/CCD init done). Fills `result`.
 *
 * NOTE (needs-hardware): the per-measurement acquisition handshake (kicking an
 * open-gate readout and reading 0x86) is implemented from the known scan kicks
 * but has not been validated on hardware for the calibration (no-motor) case.
 * Expect to tune pakon_calib_acquire on the Linux box.
 */
pakon_result pakon_calib_run(pakon_dev *dev, const pakon_calib_opts *opts,
                             pakon_calib_result *result);

#ifdef __cplusplus
}
#endif

#endif /* PAKON_CALIB_H */
