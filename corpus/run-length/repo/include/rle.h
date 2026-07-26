/* Run-length encoding over byte strings.
 *
 * The encoded form is a sequence of (count, byte) pairs, count first, written
 * as two bytes each. A run longer than 255 is split. That format is the
 * contract; how the encoder builds its output is not.
 */
#ifndef RLE_H
#define RLE_H

#include <stddef.h>

/* Encode `in` (NUL-terminated). Returns a newly allocated buffer of
 * `*out_len` bytes, or NULL on allocation failure. Caller frees. */
unsigned char *rle_encode(const char *in, size_t *out_len);

/* Decode `len` bytes. Returns a newly allocated NUL-terminated string, or NULL
 * on allocation failure. Caller frees. */
char *rle_decode(const unsigned char *in, size_t len);

/* Number of bytes rle_encode would produce for `in`. */
size_t rle_encoded_size(const char *in);

#endif /* RLE_H */
