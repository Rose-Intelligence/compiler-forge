/* Internal helpers. Not part of the public contract: an optimization may change
 * or remove them provided the encoder still produces the same bytes. */
#ifndef RLE_INTERNAL_H
#define RLE_INTERNAL_H

#include <stddef.h>

size_t rle_run_length(const char *s, size_t at, size_t len);

#endif /* RLE_INTERNAL_H */
