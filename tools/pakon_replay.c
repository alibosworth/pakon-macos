/*
 * pakon_replay — replays/extends captured handshakes against the device.
 *
 * --open replays the open handshake decoded from a real scan capture (the EP1
 * command exchanges that bring the device to Idle) and verifies each reply,
 * exercising the pakon_cmd primitive end to end. --scan (Phase 5) is still a
 * stub pending the image-stream decode.
 *
 * NOTE: this requires the OPERATIONAL device (0F05:F135). The f235 bootstrap
 * does not implement the command protocol.
 */
#include "pakon_usb.h"
#include "pakon_proto.h"
#include "pakon_cmd.h"
#include "pakon_log.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* One step of the captured open handshake: the bytes to send, and the reply
 * bytes observed in the capture (for verification). Lengths are 2+count. */
typedef struct {
    const char *label;
    uint8_t out[8];
    size_t  out_len;
    uint8_t expect[8];
    size_t  expect_len;
} open_step;

/* From the device-13 scan capture (see docs/PROTOCOL.md). */
static const open_step OPEN_SEQ[] = {
    {"open",            {0x04,0x03,0x10,0x00,0x85}, 5, {0x07,0x02,0x10,0x00}, 4},
    {"open-2",          {0x02,0x04,0x10,0x01,0x8f,0x00}, 6, {0x07,0x02,0x10,0x00}, 4},
    {"probe PICM_PLUS", {0x04,0x03,0x44,0x00,0x00}, 5, {0x07,0x02,0x44,0x01}, 4},
    {"probe BOOT_PICM_PLUS", {0x04,0x03,0x46,0x00,0x00}, 5, {0x07,0x02,0x46,0x01}, 4},
    {"probe PICM",      {0x04,0x03,0x24,0x00,0x00}, 5, {0x07,0x02,0x24,0x00}, 4},
};

static void hex(const char *tag, const uint8_t *b, size_t n)
{
    printf("%s", tag);
    for (size_t i = 0; i < n; i++)
        printf(" %02x", b[i]);
}

static int do_open(unsigned timeout)
{
    pakon_ctx *ctx = NULL;
    if (pakon_usb_init(&ctx) != PAKON_OK) {
        fprintf(stderr, "init failed\n");
        return 1;
    }

    pakon_dev *dev = NULL;
    pakon_result r = pakon_usb_open(ctx, &dev);
    if (r != PAKON_OK) {
        fprintf(stderr, "open device failed: %s "
                "(need operational 0F05:F135 on the host)\n",
                pakon_result_str(r));
        pakon_usb_exit(ctx);
        return 1;
    }

    uint16_t vid = 0, pid = 0;
    pakon_usb_dev_ids(dev, &vid, &pid);
    printf("device %04x:%04x\n", vid, pid);

    r = pakon_usb_claim(dev, 0, 0);
    if (r != PAKON_OK) {
        fprintf(stderr, "claim failed: %s\n", pakon_result_str(r));
        pakon_usb_close(dev);
        pakon_usb_exit(ctx);
        return 1;
    }

    int failures = 0;
    size_t n = sizeof(OPEN_SEQ) / sizeof(OPEN_SEQ[0]);
    for (size_t i = 0; i < n; i++) {
        const open_step *s = &OPEN_SEQ[i];
        pakon_packet cmd, reply;
        /* out[0]=type, out[1]=count, out[2..]=data */
        pakon_packet_build(&cmd, s->out[0], s->out + 2, s->out[1]);

        r = pakon_cmd(dev, &cmd, &reply, timeout);
        printf("[%-20s] ", s->label);
        hex("send", s->out, s->out_len);
        if (r != PAKON_OK) {
            printf("  -> ERROR %s\n", pakon_result_str(r));
            failures++;
            continue;
        }
        uint8_t got[PAKON_PACKET_SIZE];
        size_t glen = 0;
        pakon_packet_serialize(&reply, got, sizeof(got), &glen);
        hex("  recv", got, glen);
        if (glen == s->expect_len && memcmp(got, s->expect, glen) == 0) {
            printf("  OK\n");
        } else {
            hex("  (expected", s->expect, s->expect_len);
            printf(")  MISMATCH\n");
            failures++;
        }
    }

    pakon_usb_release(dev);
    pakon_usb_close(dev);
    pakon_usb_exit(ctx);

    printf("\nopen handshake: %s (%d/%zu steps mismatched)\n",
           failures ? "INCOMPLETE" : "reached Idle", failures, n);
    return failures ? 1 : 0;
}

static int hexbytes(const char *s, uint8_t *out, size_t max);

/* ---- Film advance: replay an advance script (.pakscan, O/C lines only) ---- */

static int do_advance(const char *script, unsigned timeout, unsigned limit_sec,
                      unsigned long steps_count)
{
    FILE *fp = fopen(script, "r");
    if (!fp) { fprintf(stderr, "cannot open script '%s'\n", script); return 1; }

    pakon_ctx *ctx = NULL;
    if (pakon_usb_init(&ctx) != PAKON_OK) { fclose(fp); return 1; }
    pakon_dev *dev = NULL;
    pakon_result r = pakon_usb_open(ctx, &dev);
    if (r != PAKON_OK) {
        fprintf(stderr, "open device failed: %s (need operational f135)\n",
                pakon_result_str(r));
        fclose(fp); pakon_usb_exit(ctx); return 1;
    }
    if (pakon_usb_claim(dev, 0, 0) != PAKON_OK) {
        fprintf(stderr, "claim failed\n");
        pakon_usb_close(dev); fclose(fp); pakon_usb_exit(ctx); return 1;
    }

    char line[1024];
    uint8_t buf[65536];
    unsigned long ncmd = 0, errs = 0;
    int rc = 0;

    /* Replay the captured command sequence. */
    while (fgets(line, sizeof(line), fp)) {
        char *p = line;
        while (*p == ' ' || *p == '\t') p++;
        if (*p == '#' || *p == '\n' || *p == '\0') continue;

        if (*p == 'O') {
            int n = hexbytes(p + 1, buf, sizeof(buf));
            if (n < 0) { fprintf(stderr, "bad O line\n"); rc = 1; break; }
            size_t sent = 0;
            r = pakon_usb_send(dev, PAKON_EP_CMD_OUT, buf, (size_t)n, &sent, timeout);
            if (r == PAKON_OK) {
                size_t got = 0;
                r = pakon_usb_recv(dev, PAKON_EP_CMD_IN, buf, sizeof(buf), &got, timeout);
            }
            if (r != PAKON_OK) errs++;
            ncmd++;
        } else if (*p == 'C') {
            unsigned brt, breq, wval, widx, wlen; int consumed = 0;
            if (sscanf(p + 1, "%x %x %x %x %x%n", &brt,&breq,&wval,&widx,&wlen,&consumed) != 5) {
                fprintf(stderr, "bad C line\n"); rc = 1; break;
            }
            int dn = hexbytes(p + 1 + consumed, buf, sizeof(buf));
            if (dn < 0) dn = 0;
            size_t got = 0;
            r = pakon_usb_control(dev, (uint8_t)brt, (uint8_t)breq, (uint16_t)wval,
                                  (uint16_t)widx, buf, (uint16_t)wlen, &got, timeout);
            if (r != PAKON_OK) errs++;
        }
    }
    fclose(fp);

    /* Each advance step: a0 (start) → poll HOST until PS_SUCCESS (frame in
     * position) → a2 (finalize/stop).  Mirrors what the captured script does
     * in a single step, repeated for steps_count frames. */
    if (!rc) {
        /* start-advance: type=0x04, data=[PICM, 0x00, 0xa0] */
        uint8_t adv_data[] = {AD_PICM, 0x00, 0xa0};
        pakon_packet adv_pkt, adv_reply;
        pakon_packet_build(&adv_pkt, 0x04, adv_data, 3);

        /* finalize-advance: type=0x04, data=[PICM, 0x00, 0xa2] */
        uint8_t fin_data[] = {AD_PICM, 0x00, 0xa2};
        pakon_packet fin_pkt, fin_reply;
        pakon_packet_build(&fin_pkt, 0x04, fin_data, 3);

        /* HOST status poll: type=0x03, data=[HOST] */
        uint8_t host_data[] = {AD_HOST};
        pakon_packet host_pkt, host_reply;
        pakon_packet_build(&host_pkt, 0x03, host_data, 1);

        time_t t0 = time(NULL);
        unsigned long steps = 0;
        printf("  [advance] %lu step(s), limit %u s\n",
               steps_count, limit_sec);

        for (unsigned long s = 0; s < steps_count; s++) {
            time_t elapsed = time(NULL) - t0;
            if (limit_sec > 0 && (unsigned long)elapsed >= limit_sec) {
                printf("  [advance] time limit reached after %ld s\n", (long)elapsed);
                rc = 1;
                break;
            }

            /* Start the step. */
            r = pakon_cmd(dev, &adv_pkt, &adv_reply, timeout);
            ncmd++;
            if (r != PAKON_OK) { errs++; break; }

            /* Poll HOST until it signals frame in position (PS_SUCCESS). */
            for (int p = 0; p < 5000; p++) {
                r = pakon_cmd(dev, &host_pkt, &host_reply, timeout);
                ncmd++;
                if (r != PAKON_OK) { errs++; break; }
                uint8_t st = pakon_packet_status(&host_reply);
                if (st == PS_SUCCESS) break;
            }

            /* Finalize/stop the step. */
            r = pakon_cmd(dev, &fin_pkt, &fin_reply, timeout);
            ncmd++;
            if (r != PAKON_OK) errs++;

            steps++;
            printf("  step %lu/%lu done (%ld s elapsed)\n",
                   steps, steps_count, (long)(time(NULL) - t0));
        }
        printf("  [advance] %lu/%lu steps in %ld s\n",
               steps, steps_count, (long)(time(NULL) - t0));
    }

    pakon_usb_release(dev);
    pakon_usb_close(dev);
    pakon_usb_exit(ctx);
    printf("advance done: %lu commands (%lu errors)\n", ncmd, errs);
    return rc || errs ? 1 : 0;
}

/* ---- Phase 5: replay a full scan operation script (.pakscan) ---- */

#define SCAN_IMG_TIMEOUT_MS 5000

/* Parse a hex byte string into out[max]; returns count or -1. */
static int hexbytes(const char *s, uint8_t *out, size_t max)
{
    size_t n = 0;
    int hi = -1;
    for (; *s && *s != '\n' && *s != '\r'; s++) {
        if (*s == ' ') continue;
        int v = (*s >= '0' && *s <= '9') ? *s - '0'
              : (*s >= 'a' && *s <= 'f') ? *s - 'a' + 10
              : (*s >= 'A' && *s <= 'F') ? *s - 'A' + 10 : -1;
        if (v < 0) return -1;
        if (hi < 0) hi = v;
        else { if (n >= max) return -1; out[n++] = (uint8_t)((hi<<4)|v); hi = -1; }
    }
    return hi < 0 ? (int)n : -1;
}

/* After the script ends, keep polling + draining 0x86 until it goes quiet.
 * Used for scans longer than the captured reference (e.g. whole rolls). */
static void drain_image(pakon_dev *dev, FILE *img,
                        unsigned long *ncmd, unsigned long *nimg,
                        unsigned long long *img_bytes, unsigned long *errs,
                        unsigned timeout)
{
    /* 03 01 10 = status poll HOST — the tight loop command from the capture */
    uint8_t poll_data[] = {0x10};
    pakon_packet poll_pkt, poll_reply;
    pakon_packet_build(&poll_pkt, 0x03, poll_data, 1);

    uint8_t buf[20480];
    for (;;) {
        pakon_result r = pakon_cmd(dev, &poll_pkt, &poll_reply, timeout);
        if (r != PAKON_OK) (*errs)++;
        (*ncmd)++;

        size_t got = 0;
        r = pakon_usb_recv(dev, PAKON_EP_IMAGE_IN, buf, sizeof(buf), &got,
                           SCAN_IMG_TIMEOUT_MS);
        if (got) { fwrite(buf, 1, got, img); *img_bytes += got; }
        if (r != PAKON_OK && r != PAKON_ERR_TIMEOUT) (*errs)++;
        if (++(*nimg) % 1000 == 0)
            printf("  ... %lu image reads, %llu bytes\n", *nimg, *img_bytes);
        if (got == 0 || r == PAKON_ERR_TIMEOUT) break;
    }
}

/* Is this command one whose live reply we want to trace? Polls (03 01 xx) and
 * the engine kicks (8a/92 readout, a0/a2 motor). */
static int trace_interesting(const uint8_t *raw, int n)
{
    if (n >= 3 && raw[0] == 0x03 && raw[1] == 0x01) return 1;          /* poll  */
    if (n >= 5 && raw[0] == 0x04 && raw[1] == 0x03 &&
        (raw[4] == 0x8a || raw[4] == 0x92 || raw[4] == 0xa0 || raw[4] == 0xa2))
        return 1;                                                      /* kick  */
    return 0;
}

static int do_scan(const char *script, const char *image_path, unsigned timeout,
                   int drain, int trace_status)
{
    FILE *fp = fopen(script, "r");
    if (!fp) { fprintf(stderr, "cannot open scan script '%s'\n", script); return 1; }

    pakon_ctx *ctx = NULL;
    if (pakon_usb_init(&ctx) != PAKON_OK) { fclose(fp); return 1; }
    pakon_dev *dev = NULL;
    pakon_result r = pakon_usb_open(ctx, &dev);
    if (r != PAKON_OK) {
        fprintf(stderr, "open device failed: %s (need operational f135)\n",
                pakon_result_str(r));
        fclose(fp); pakon_usb_exit(ctx); return 1;
    }
    if (pakon_usb_claim(dev, 0, 0) != PAKON_OK) {
        fprintf(stderr, "claim failed\n");
        pakon_usb_close(dev); fclose(fp); pakon_usb_exit(ctx); return 1;
    }

    FILE *img = fopen(image_path, "wb");
    if (!img) {
        fprintf(stderr, "cannot open image '%s'\n", image_path);
        pakon_usb_release(dev); pakon_usb_close(dev); fclose(fp);
        pakon_usb_exit(ctx); return 1;
    }

    char line[1024];
    uint8_t buf[65536];
    unsigned long ncmd = 0, nimg = 0, errs = 0;
    unsigned long long img_bytes = 0;
    int rc = 0;

    while (fgets(line, sizeof(line), fp)) {
        char *p = line;
        while (*p == ' ' || *p == '\t') p++;
        if (*p == '#' || *p == '\n' || *p == '\0') continue;

        if (*p == 'O') {                                  /* command + reply */
            int n = hexbytes(p + 1, buf, sizeof(buf));
            if (n < 0) { fprintf(stderr, "bad O line\n"); rc = 1; break; }
            /* Save the command bytes before the reply overwrites buf. */
            uint8_t snd[8]; int sn = n < (int)sizeof(snd) ? n : (int)sizeof(snd);
            memcpy(snd, buf, (size_t)sn);
            int trace = trace_status && trace_interesting(snd, n);
            size_t sent = 0, got = 0;
            r = pakon_usb_send(dev, PAKON_EP_CMD_OUT, buf, (size_t)n, &sent, timeout);
            if (r == PAKON_OK)
                r = pakon_usb_recv(dev, PAKON_EP_CMD_IN, buf, sizeof(buf), &got, timeout);
            if (r != PAKON_OK) errs++;
            if (trace) {
                printf("  [trace @img=%lu] send", nimg);
                for (int k = 0; k < sn; k++) printf(" %02x", snd[k]);
                if (r == PAKON_OK) {
                    printf("  reply");
                    for (size_t k = 0; k < got; k++) printf(" %02x", buf[k]);
                    printf("  (status=%u)", got >= 4 ? buf[3] : 0);
                } else {
                    printf("  -> %s", pakon_result_str(r));
                }
                printf("\n");
            }
            ncmd++;
        } else if (*p == 'M') {                           /* image read */
            unsigned long want = strtoul(p + 1, NULL, 0);
            if (want > sizeof(buf)) want = sizeof(buf);
            size_t got = 0;
            r = pakon_usb_recv(dev, PAKON_EP_IMAGE_IN, buf, want, &got, SCAN_IMG_TIMEOUT_MS);
            if (got) { fwrite(buf, 1, got, img); img_bytes += got; }
            if (r != PAKON_OK && r != PAKON_ERR_TIMEOUT) errs++;
            if (++nimg % 1000 == 0)
                printf("  ... %lu image reads, %llu bytes\n", nimg, img_bytes);
        } else if (*p == 'C') {                           /* control xfer */
            unsigned brt, breq, wval, widx, wlen; int consumed = 0;
            if (sscanf(p + 1, "%x %x %x %x %x%n", &brt,&breq,&wval,&widx,&wlen,&consumed) != 5) {
                fprintf(stderr, "bad C line\n"); rc = 1; break;
            }
            int dn = hexbytes(p + 1 + consumed, buf, sizeof(buf));
            if (dn < 0) dn = 0;
            size_t got = 0;
            r = pakon_usb_control(dev, (uint8_t)brt, (uint8_t)breq, (uint16_t)wval,
                                  (uint16_t)widx, buf, (uint16_t)wlen, &got, timeout);
            if (r != PAKON_OK) errs++;
        }
    }

    if (!rc && drain) {
        printf("  [drain] script exhausted, draining 0x86 until scanner signals done...\n");
        drain_image(dev, img, &ncmd, &nimg, &img_bytes, &errs, timeout);
    }

    pakon_usb_release(dev);
    pakon_usb_close(dev);
    pakon_usb_exit(ctx);
    fclose(fp);
    fclose(img);
    printf("\nscan replay done: %lu commands, %lu image reads, %llu image bytes "
           "-> %s (%lu transfer errors)\n",
           ncmd, nimg, img_bytes, image_path, errs);
    return rc;
}

/* ---- Phase 5b: poll-driven scan state machine ----------------------------
 *
 * Verbatim replay locks the scan to the captured image-read count, so it only
 * works for a roll the same length as the reference capture. The state machine
 * replays just the deterministic SETUP spine (OPEN -> param table -> the
 * calibration-derived register writes -> motor start, all of which we cannot
 * synthesise) and then DRIVES the image transfer itself, reading 0x86 and
 * re-arming the CCD until the film has fully passed.
 *
 * Done-signal: we cannot mine it offline (the .pakscan stores commands sent,
 * never the poll replies, and test/captures is empty), so we use the IMAGE
 * data: the scan ends with the open gate shining through no film (samples near
 * the 16-bit max, ~48900; film -- even clear base/leader -- is far darker). We
 * latch "film seen" on the first non-white chunk, then stop once we have seen a
 * sustained run of end-of-roll white. Every status poll is logged so a hardware
 * run also reveals the real protocol done-signal for a future tightening.
 *
 * Split marker between replay and takeover = the motor-start command
 * 04 03 24 00 a0 (confirmed). Everything up to the first image read after it is
 * replayed; from there we take over. */

/* Tunables (logged at start; see docs/PROTOCOL.md photometry + status notes). */
#define SM_WHITE_THRESH   40000u  /* 16-bit sample > this => open-gate "white" */
#define SM_WHITE_FRAC_PCT 90u     /* chunk is white if >= this %% of samples are */
#define SM_TRAIL_WHITE    8u      /* consecutive white chunks after film => done */
#define SM_MAX_EMPTY      3u      /* consecutive ready(0x00)-but-empty reads => done */
#define SM_MAX_BUSY       600u    /* consecutive busy(0x80) polls w/o data => give up */
#define SM_HOST_BUSY      0x80u   /* HOST status: busy/buffer-starved (else 0x00) */

static const uint8_t SM_MOTOR_START[] = {0x04,0x03,0x24,0x00,0xa0};

/* Is a freshly read 0x86 chunk dominated by open-gate white (no film)? */
static int sm_chunk_is_white(const uint8_t *buf, size_t got)
{
    size_t samples = got / 2;
    if (samples == 0) return 0;
    size_t white = 0;
    for (size_t i = 0; i + 1 < got; i += 2) {
        unsigned v = (unsigned)buf[i] | ((unsigned)buf[i + 1] << 8);
        if (v > SM_WHITE_THRESH) white++;
    }
    return white * 100u >= samples * SM_WHITE_FRAC_PCT;
}

/* Build + send a frame whose wire bytes are raw[0..n) (raw[0]=type,[1]=count,
 * [2..]=data). Reads (and discards) the reply on EP1 IN. */
static pakon_result sm_cmd(pakon_dev *dev, const uint8_t *raw, size_t n,
                           pakon_packet *reply, unsigned timeout)
{
    (void)n;   /* count is carried in raw[1]; n is the caller's sizeof sanity */
    pakon_packet pkt;
    pakon_packet_build(&pkt, raw[0], raw + 2, raw[1]);
    return pakon_cmd(dev, &pkt, reply, timeout);
}

/* The poll-driven image phase.
 *
 * CONFIRMED from a full --trace-status scan: re-arming (8a) happens ONLY in the
 * preview/calibration phase (all 21 re-arms at <= the motor-start read index);
 * once the motor runs the CCD streams continuously with ZERO re-arms until the
 * film ends. So here we NEVER send a state-changing command -- we only read
 * 0x86 and issue read-only HOST status polls. (The earlier re-arm-on-empty
 * design wedged the bus by writing into a live stream.)
 *
 * HOST status: 0x00 = ready, 0x80 = busy (buffer momentarily starved). The
 * blocking bulk read absorbs busy (device NAKs, libusb waits), so a real read
 * timeout means no data. On timeout we poll HOST: busy => keep waiting; ready
 * => nothing left. Primary stop is end-of-roll white. */
static void sm_scan_loop(pakon_dev *dev, FILE *img, unsigned long long *img_bytes,
                         unsigned long *nimg, unsigned long *ncmd, unsigned long *errs,
                         unsigned timeout, unsigned long max_mb, int *film_seen_out)
{
    const uint8_t poll_host[] = {0x03,0x01,0x10};

    uint8_t buf[20480];
    int film_seen = 0;
    unsigned trail_white = 0, idle = 0, busy = 0;
    unsigned long long max_bytes = (unsigned long long)max_mb * 1024u * 1024u;
    pakon_packet reply;

    printf("  [sm] image phase: read 0x86 (no re-arm; CCD armed in setup); stop "
           "on %u trailing-white chunks, %u ready-empty reads, or %lu MB cap\n",
           SM_TRAIL_WHITE, SM_MAX_EMPTY, max_mb);

    for (;;) {
        size_t got = 0;
        pakon_result r = pakon_usb_recv(dev, PAKON_EP_IMAGE_IN, buf, sizeof(buf),
                                        &got, SCAN_IMG_TIMEOUT_MS);
        if (got) {
            fwrite(buf, 1, got, img);
            *img_bytes += got;
            (*nimg)++;
            idle = busy = 0;
            if (sm_chunk_is_white(buf, got)) {
                if (film_seen && ++trail_white >= SM_TRAIL_WHITE) {
                    printf("  [sm] end-of-roll white (%u chunks) after %llu bytes "
                           "-> film fully scanned\n", trail_white, *img_bytes);
                    break;
                }
            } else {
                if (!film_seen)
                    printf("  [sm] film detected at %llu bytes\n", *img_bytes);
                film_seen = 1;
                trail_white = 0;
            }
            if (max_bytes && *img_bytes >= max_bytes) {
                printf("  [sm] hit safety cap %lu MB -> stopping\n", max_mb);
                break;
            }
            continue;
        }
        if (r != PAKON_OK && r != PAKON_ERR_TIMEOUT) (*errs)++;

        /* No data this window: ask HOST whether it's still working. Read-only,
         * so this can never wedge the command channel mid-stream. */
        uint8_t st = 0xff;
        if (sm_cmd(dev, poll_host, sizeof(poll_host), &reply, timeout) == PAKON_OK)
            st = pakon_packet_status(&reply);
        else (*errs)++;
        (*ncmd)++;

        if (st == SM_HOST_BUSY) {                 /* still feeding -- wait more */
            if (++busy >= SM_MAX_BUSY) {
                printf("  [sm] HOST busy for %u polls with no data -> giving up\n", busy);
                break;
            }
            continue;
        }
        printf("  [sm] empty read, HOST st=0x%02x (idle %u/%u)\n",
               st, idle + 1, SM_MAX_EMPTY);
        if (++idle >= SM_MAX_EMPTY) {             /* ready but nothing left */
            printf("  [sm] no more image data -> done\n");
            break;
        }
    }
    if (film_seen_out) *film_seen_out = film_seen;
}

static int do_scan_sm(const char *script, const char *image_path, unsigned timeout,
                      unsigned long max_mb)
{
    FILE *fp = fopen(script, "r");
    if (!fp) { fprintf(stderr, "cannot open scan script '%s'\n", script); return 1; }

    pakon_ctx *ctx = NULL;
    if (pakon_usb_init(&ctx) != PAKON_OK) { fclose(fp); return 1; }
    pakon_dev *dev = NULL;
    pakon_result r = pakon_usb_open(ctx, &dev);
    if (r != PAKON_OK) {
        fprintf(stderr, "open device failed: %s (need operational f135)\n",
                pakon_result_str(r));
        fclose(fp); pakon_usb_exit(ctx); return 1;
    }
    if (pakon_usb_claim(dev, 0, 0) != PAKON_OK) {
        fprintf(stderr, "claim failed\n");
        pakon_usb_close(dev); fclose(fp); pakon_usb_exit(ctx); return 1;
    }

    FILE *img = fopen(image_path, "wb");
    if (!img) {
        fprintf(stderr, "cannot open image '%s'\n", image_path);
        pakon_usb_release(dev); pakon_usb_close(dev); fclose(fp);
        pakon_usb_exit(ctx); return 1;
    }

    char line[1024];
    uint8_t buf[65536];
    unsigned long ncmd = 0, nimg = 0, errs = 0;
    unsigned long long img_bytes = 0;
    int rc = 0, passed_motor_start = 0, took_over = 0;

    /* Phase 1: replay the deterministic setup spine, including preview/cal
     * image reads, up to the first image read AFTER motor start. */
    while (fgets(line, sizeof(line), fp)) {
        char *p = line;
        while (*p == ' ' || *p == '\t') p++;
        if (*p == '#' || *p == '\n' || *p == '\0') continue;

        if (*p == 'O') {
            int n = hexbytes(p + 1, buf, sizeof(buf));
            if (n < 0) { fprintf(stderr, "bad O line\n"); rc = 1; break; }
            /* Note the motor-start marker before the reply overwrites buf. */
            int is_motor_start = ((size_t)n == sizeof(SM_MOTOR_START) &&
                                  memcmp(buf, SM_MOTOR_START, n) == 0);
            size_t sent = 0;
            r = pakon_usb_send(dev, PAKON_EP_CMD_OUT, buf, (size_t)n, &sent, timeout);
            if (r == PAKON_OK) {
                size_t got = 0;
                r = pakon_usb_recv(dev, PAKON_EP_CMD_IN, buf, sizeof(buf), &got, timeout);
            }
            if (r != PAKON_OK) errs++;
            ncmd++;
            if (is_motor_start) {
                passed_motor_start = 1;
                printf("  [sm] motor start (a0) replayed at cmd %lu\n", ncmd);
            }
        } else if (*p == 'M') {
            if (passed_motor_start) {           /* hand off to the poll loop */
                printf("  [sm] setup replayed (%lu cmds); taking over scan\n", ncmd);
                took_over = 1;
                break;
            }
            unsigned long want = strtoul(p + 1, NULL, 0);
            if (want > sizeof(buf)) want = sizeof(buf);
            size_t got = 0;
            r = pakon_usb_recv(dev, PAKON_EP_IMAGE_IN, buf, want, &got, SCAN_IMG_TIMEOUT_MS);
            if (got) { fwrite(buf, 1, got, img); img_bytes += got; }
            if (r != PAKON_OK && r != PAKON_ERR_TIMEOUT) errs++;
            nimg++;
        } else if (*p == 'C') {
            unsigned brt, breq, wval, widx, wlen; int consumed = 0;
            if (sscanf(p + 1, "%x %x %x %x %x%n", &brt,&breq,&wval,&widx,&wlen,&consumed) != 5) {
                fprintf(stderr, "bad C line\n"); rc = 1; break;
            }
            int dn = hexbytes(p + 1 + consumed, buf, sizeof(buf));
            if (dn < 0) dn = 0;
            size_t got = 0;
            r = pakon_usb_control(dev, (uint8_t)brt, (uint8_t)breq, (uint16_t)wval,
                                  (uint16_t)widx, buf, (uint16_t)wlen, &got, timeout);
            if (r != PAKON_OK) errs++;
        }
    }
    fclose(fp);

    /* Phase 2: poll-driven image transfer until end-of-roll white. */
    if (!rc) {
        if (!passed_motor_start)
            printf("  [sm] no motor-start (a0) in script; taking over at end of "
                   "script\n");
        int film_seen = 0;
        sm_scan_loop(dev, img, &img_bytes, &nimg, &ncmd, &errs, timeout, max_mb,
                     &film_seen);
        if (!film_seen)
            fprintf(stderr, "  [sm] WARNING: no film ever detected in the stream "
                    "(stale device state / nothing loaded?)\n");

        /* Phase 3: stop the engines (halt readout, then motor). The loop only
         * issued read-only polls, so the command channel is still alive here. */
        uint8_t stop_readout[] = {0x04,0x03,0x20,0x00,0x92};
        uint8_t stop_motor[]   = {0x04,0x03,0x24,0x00,0xa2};
        sm_cmd(dev, stop_readout, sizeof(stop_readout), NULL, timeout);
        sm_cmd(dev, stop_motor,   sizeof(stop_motor),   NULL, timeout);
        ncmd += 2;
        printf("  [sm] sent stop (readout 92, motor a2)\n");
    }
    (void)took_over;

    pakon_usb_release(dev);
    pakon_usb_close(dev);
    pakon_usb_exit(ctx);
    fclose(img);
    printf("\nscan-sm done: %lu commands, %lu image reads, %llu image bytes "
           "-> %s (%lu transfer errors)\n",
           ncmd, nimg, img_bytes, image_path, errs);
    return rc;
}

static void usage(const char *argv0)
{
    fprintf(stderr,
        "usage: %s FILE.pakscan [--steps N] [--limit SEC] advance film\n"
        "       %s --scan FILE [--image OUT] [--drain]   verbatim scan replay\n"
        "       %s --scan-sm FILE [--image OUT] [--max-mb N]  poll-driven scan\n"
        "       %s --open                                verify open handshake\n"
        "\n"
        "  FILE.pakscan  positional: replay advance script, then poll until idle\n"
        "  --limit SEC   wall-clock limit for the advance poll loop (default 60)\n"
        "  --open        replay the captured open handshake, verify replies\n"
        "  --scan FILE   replay a .pakscan scan script verbatim (fixed length)\n"
        "  --scan-sm FILE  replay setup spine, then drive the image transfer and\n"
        "                  stop on end-of-roll white (any roll length)\n"
        "  --image OUT   raw image output for --scan/--scan-sm (default pakon_scan.raw)\n"
        "  --drain       after the scan script ends, keep reading 0x86 until done\n"
        "  --trace-status  with --scan: log live poll/kick replies + status, with\n"
        "                  the current image-read index (to learn the cadence)\n"
        "  --max-mb N    --scan-sm safety cap on image bytes (default 512, 0=off)\n"
        "  --timeout MS  USB per-transfer timeout in ms (default 1000)\n"
        "\n"
        "Set PAKON_DEBUG=0..4 for increasing trace verbosity.\n",
        argv0, argv0, argv0, argv0);
}

int main(int argc, char **argv)
{
    int want_open = 0, drain = 0, trace_status = 0;
    const char *scan_file = NULL;
    const char *scan_sm_file = NULL;
    const char *advance_file = NULL;
    const char *image_path = "pakon_scan.raw";
    unsigned timeout = 1000;
    unsigned limit_sec = 60;
    unsigned long steps_count = 1;
    unsigned long max_mb = 512;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) {
            usage(argv[0]);
            return 0;
        } else if (!strcmp(argv[i], "--open")) {
            want_open = 1;
        } else if (!strcmp(argv[i], "--scan") && i + 1 < argc) {
            scan_file = argv[++i];
        } else if (!strcmp(argv[i], "--scan-sm") && i + 1 < argc) {
            scan_sm_file = argv[++i];
        } else if (!strcmp(argv[i], "--max-mb") && i + 1 < argc) {
            max_mb = strtoul(argv[++i], NULL, 0);
        } else if (!strcmp(argv[i], "--image") && i + 1 < argc) {
            image_path = argv[++i];
        } else if (!strcmp(argv[i], "--drain")) {
            drain = 1;
        } else if (!strcmp(argv[i], "--trace-status")) {
            trace_status = 1;
        } else if (!strcmp(argv[i], "--timeout") && i + 1 < argc) {
            timeout = (unsigned)strtoul(argv[++i], NULL, 0);
        } else if (!strcmp(argv[i], "--limit") && i + 1 < argc) {
            limit_sec = (unsigned)strtoul(argv[++i], NULL, 0);
        } else if (!strcmp(argv[i], "--steps") && i + 1 < argc) {
            steps_count = strtoul(argv[++i], NULL, 0);
        } else if (argv[i][0] != '-' && !advance_file) {
            advance_file = argv[i];
        } else {
            fprintf(stderr, "unknown/incomplete argument: %s\n", argv[i]);
            usage(argv[0]);
            return 2;
        }
    }

    if (!want_open && !scan_file && !scan_sm_file && !advance_file) {
        usage(argv[0]);
        return 2;
    }

    if (advance_file)
        return do_advance(advance_file, timeout, limit_sec, steps_count);
    if (scan_sm_file)
        return do_scan_sm(scan_sm_file, image_path, timeout, max_mb);
    if (scan_file)
        return do_scan(scan_file, image_path, timeout, drain, trace_status);
    return do_open(timeout);
}
