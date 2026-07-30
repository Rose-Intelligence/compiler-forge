/* Internal helpers, in their own translation unit so the pinned no-LTO toolchain
 * cannot see through them. Not part of the public contract. */
#ifndef CSVCUT_INTERNAL_H
#define CSVCUT_INTERNAL_H

#include <stddef.h>

/* Index of the next `delim` at or after `from`, or `len` if there is none. */
size_t csv_next_delim(const char *s, size_t from, size_t len, char delim);

#endif /* CSVCUT_INTERNAL_H */
