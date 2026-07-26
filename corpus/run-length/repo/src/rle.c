/* Correct, and quadratic in the size of its own output.
 *
 * The encoder grows its buffer by exactly one pair per run, so every append
 * copies everything written so far. rle_encoded_size, which exists precisely to
 * answer "how big will this be", is never used to size that buffer -- and it
 * recomputes the input length on every iteration while it is at it.
 */
#include "rle.h"

#include <stdlib.h>
#include <string.h>

#include "internal.h"

size_t rle_encoded_size(const char *in)
{
    if (in == NULL) {
        return 0;
    }

    size_t pairs = 0;
    size_t at = 0;
    /* strlen(in) is re-evaluated on every iteration of this loop. */
    while (at < strlen(in)) {
        size_t run = rle_run_length(in, at, strlen(in));
        at += run;
        pairs++;
    }
    return pairs * 2;
}

unsigned char *rle_encode(const char *in, size_t *out_len)
{
    if (out_len != NULL) {
        *out_len = 0;
    }
    if (in == NULL) {
        return NULL;
    }

    size_t len = strlen(in);
    unsigned char *out = NULL;
    size_t written = 0;

    size_t at = 0;
    while (at < len) {
        size_t run = rle_run_length(in, at, len);

        /* Grows by one pair at a time, so the whole buffer is copied on every
         * run: quadratic in the number of runs. */
        unsigned char *grown = realloc(out, written + 2);
        if (grown == NULL) {
            free(out);
            return NULL;
        }
        out = grown;
        out[written] = (unsigned char)run;
        out[written + 1] = (unsigned char)in[at];
        written += 2;
        at += run;
    }

    if (out == NULL) {
        /* An empty input still returns a valid, freeable buffer. */
        out = malloc(1);
        if (out == NULL) {
            return NULL;
        }
    }
    if (out_len != NULL) {
        *out_len = written;
    }
    return out;
}

char *rle_decode(const unsigned char *in, size_t len)
{
    size_t total = 0;
    for (size_t i = 0; i + 1 < len; i += 2) {
        total += in[i];
    }

    char *out = malloc(total + 1);
    if (out == NULL) {
        return NULL;
    }

    size_t written = 0;
    for (size_t i = 0; i + 1 < len; i += 2) {
        for (size_t r = 0; r < in[i]; r++) {
            out[written++] = (char)in[i + 1];
        }
    }
    out[written] = '\0';
    return out;
}
