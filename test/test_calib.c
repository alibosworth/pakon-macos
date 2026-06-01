/*
 * test_calib — hardware-free unit tests for the CALIBRATE pure core.
 *
 * Validates the register-write frame builder (reproducing the OEM's exact wire
 * bytes from the capture), the AFE encoders, the deinterleave/measure helpers,
 * and the feedback math (convergence predicate, offset step, gain ratio). The
 * hardware-driving loops in pakon_calib_run are NOT exercised here (they need a
 * device); this covers everything that can be proven offline.
 */
#include "pakon_calib.h"
#include "pakon_proto.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
        if (cond) { printf("ok   - %s\n", (msg)); }             \
        else { printf("FAIL - %s\n", (msg)); failures++; }      \
    } while (0)

/* Serialize a built frame and compare to expected wire bytes. */
static int frame_is(const pakon_packet *pkt, const uint8_t *exp, size_t explen)
{
    uint8_t wire[PAKON_PACKET_SIZE];
    size_t wlen = 0;
    if (pakon_packet_serialize(pkt, wire, sizeof(wire), &wlen) != PAKON_OK)
        return 0;
    return wlen == explen && memcmp(wire, exp, explen) == 0;
}

int main(void)
{
    /* ---- frame builder reproduces the OEM's captured wire bytes ---- */
    {
        pakon_packet pkt;
        /* OEM seed: Gain_R = 13 -> 02 06 24 03 84 02 0d 00 */
        const uint8_t gr[] = {0x02,0x06,0x24,0x03,0x84,0x02,0x0d,0x00};
        CHECK(pakon_calib_build_write(&pkt, PAKON_BANK_AFE, PAKON_REG_GAIN_R,
                                      pakon_calib_enc_gain(13)) == PAKON_OK &&
              frame_is(&pkt, gr, sizeof(gr)),
              "build Gain_R=13 == 02 06 24 03 84 02 0d 00 (OEM seed)");

        /* OEM seed: Offset_R = -51 -> 02 06 24 03 84 05 33 01 */
        const uint8_t or[] = {0x02,0x06,0x24,0x03,0x84,0x05,0x33,0x01};
        CHECK(pakon_calib_build_write(&pkt, PAKON_BANK_AFE, PAKON_REG_OFFSET_R,
                                      pakon_calib_enc_offset(-51)) == PAKON_OK &&
              frame_is(&pkt, or, sizeof(or)),
              "build Offset_R=-51 == 02 06 24 03 84 05 33 01 (OEM seed)");

        /* Height -> bank 0x82 reg 6, OEM 0x0c1a -> 02 06 24 03 82 06 1a 0c */
        const uint8_t ht[] = {0x02,0x06,0x24,0x03,0x82,0x06,0x1a,0x0c};
        CHECK(pakon_calib_build_write(&pkt, PAKON_BANK_TIMING, PAKON_REG_HEIGHT,
                                      0x0c1a) == PAKON_OK &&
              frame_is(&pkt, ht, sizeof(ht)),
              "build Height=0x0c1a == 02 06 24 03 82 06 1a 0c (OEM seed)");

        CHECK(pakon_calib_build_write(&pkt, 0x80, 0, 0) == PAKON_ERR_PARAM,
              "build rejects an unknown bank");
    }

    /* ---- AFE encoders ---- */
    CHECK(pakon_calib_enc_gain(13) == 13, "enc_gain(13) == 13");
    CHECK(pakon_calib_enc_gain(100) == 0x3f, "enc_gain clamps to 6-bit (63)");
    CHECK(pakon_calib_enc_gain(-5) == 0, "enc_gain clamps negative to 0");
    CHECK(pakon_calib_enc_offset(-51) == 0x133, "enc_offset(-51) == 0x133 (sign-mag)");
    CHECK(pakon_calib_enc_offset(51) == 0x033, "enc_offset(51) == 0x033");
    CHECK(pakon_calib_enc_offset(-300) == 0x1ff, "enc_offset(-300) clamps mag to 255, neg bit");
    CHECK(pakon_calib_enc_offset(300) == 0x0ff, "enc_offset(300) clamps mag to 255");
    CHECK(pakon_calib_enc_exposure(0x2000) == 0xfff, "enc_exposure clamps to 12-bit");

    /* ---- convergence predicate (FUN_100215a0) ---- */
    CHECK(pakon_calib_converged(300, 300, 0x20, 0x20), "converged at target");
    CHECK(pakon_calib_converged(268, 300, 0x20, 0x20), "converged at lower edge (300-32)");
    CHECK(!pakon_calib_converged(267, 300, 0x20, 0x20), "not converged below lower edge");
    CHECK(pakon_calib_converged(332, 300, 0x20, 0x20), "converged at upper edge (300+32)");
    CHECK(!pakon_calib_converged(333, 300, 0x20, 0x20), "not converged above upper edge");
    /* white target: asymmetric tolerance [64000, 64000+0x800] = [64000, 66048] */
    CHECK(pakon_calib_converged(64000, 64000, 0, 0x800), "white converged at target");
    CHECK(!pakon_calib_converged(63999, 64000, 0, 0x800), "white not converged below (tol_lo=0)");
    CHECK(pakon_calib_converged(66048, 64000, 0, 0x800), "white converged at upper edge (64000+0x800)");
    CHECK(!pakon_calib_converged(66049, 64000, 0, 0x800), "white not converged past upper edge");

    /* ---- proportional offset step: (300 - mean) * 0x1400 / 0x30000 ---- */
    CHECK(pakon_calib_offset_step(300) == 0, "offset_step(300) == 0 at target");
    CHECK(pakon_calib_offset_step(0) == 7, "offset_step(0) == +7 (300*5120/196608)");
    CHECK(pakon_calib_offset_step(600) == -7, "offset_step(600) == -7 (symmetric)");
    CHECK(pakon_calib_offset_step(64000) < -1000, "offset_step drives down hard when saturated");

    /* ---- gain feedback ---- */
    CHECK(pakon_calib_gain_factor(0) == 1.0, "gain_factor(0) == 1.0");
    {
        double f32 = pakon_calib_gain_factor(32);   /* k=1/64 -> 1/(1-0.5)=2 */
        CHECK(f32 > 1.99 && f32 < 2.01, "gain_factor(32) ~= 2.0 (k=1/64)");
    }
    CHECK(pakon_calib_gain_next(1.0, 32000) == 2, "gain_next(1.0, 32000) == 2 (64000/32000)");
    CHECK(pakon_calib_gain_next(1.0, 0) == 0x3f, "gain_next(.,peak=0) == max gain");
    CHECK(pakon_calib_gain_next(1.0, 100) == 0x3f, "gain_next clamps to 6-bit max");

    /* ---- deinterleave (per-pixel order B, R, G) ---- */
    {
        /* three pixels: (B,R,G) = (1,2,3),(4,5,6),(7,8,9), 16-bit LE */
        uint8_t raw[18];
        uint16_t samp[9] = {1,2,3, 4,5,6, 7,8,9};
        for (int i = 0; i < 9; i++) {
            raw[2*i]   = (uint8_t)(samp[i] & 0xff);
            raw[2*i+1] = (uint8_t)(samp[i] >> 8);
        }
        uint16_t b[3], r[3], g[3];
        size_t n = pakon_calib_deinterleave(raw, sizeof(raw), b, r, g, 3);
        CHECK(n == 3, "deinterleave returns 3 pixels");
        CHECK(b[0]==1 && b[1]==4 && b[2]==7, "blue channel = positions 0,3,6");
        CHECK(r[0]==2 && r[1]==5 && r[2]==8, "red channel = positions 1,4,7");
        CHECK(g[0]==3 && g[1]==6 && g[2]==9, "green channel = positions 2,5,8");

        CHECK(pakon_calib_channel_mean(r, 0, 3) == 5, "channel_mean(r) == 5");
        CHECK(pakon_calib_channel_peak(g, 0, 3) == 9, "channel_peak(g) == 9");
        CHECK(pakon_calib_channel_mean(r, 1, 1) == 0, "empty window mean == 0");
    }

    printf("\n%s (%d failures)\n", failures ? "FAILED" : "all calib tests passed",
           failures);
    return failures ? 1 : 0;
}
