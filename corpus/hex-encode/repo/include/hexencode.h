/* Lowercase hex encoding of a byte buffer.
 *
 * The handle is opaque; an optimization may precompute whatever table it likes
 * as long as hx_encode produces the same bytes.
 */
#ifndef HEXENCODE_H
#define HEXENCODE_H

#include <stddef.h>

typedef struct hx_encoder hx_encoder;

hx_encoder *hx_new(void);
void hx_free(hx_encoder *enc);

/* Encode `n` bytes as lowercase hex into `out` (must hold 2n + 1). Writes a NUL
 * and returns 2n. */
size_t hx_encode(const hx_encoder *enc, const unsigned char *data, size_t n, char *out);

#endif /* HEXENCODE_H */
