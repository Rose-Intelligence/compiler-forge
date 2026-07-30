/* Correct, and recomputing each byte's two hex digits every call.
 *
 * The digit for a nibble never changes. Precomputing the 256 two-char encodings
 * once turns each byte into a table read; the baseline instead branches per
 * nibble for every byte of every buffer.
 */
#include "hexencode.h"

#include <stdlib.h>

#include "internal.h"

struct hx_encoder {
    int ready;
};

hx_encoder *hx_new(void)
{
    hx_encoder *enc = calloc(1, sizeof *enc);
    if (enc != NULL) {
        enc->ready = 1;
    }
    return enc;
}

void hx_free(hx_encoder *enc)
{
    free(enc);
}

size_t hx_encode(const hx_encoder *enc, const unsigned char *data, size_t n, char *out)
{
    (void)enc;
    for (size_t i = 0; i < n; i++) {
        hx_byte(data[i], out + 2 * i);   /* recompute both digits, every byte */
    }
    out[2 * n] = '\0';
    return 2 * n;
}
