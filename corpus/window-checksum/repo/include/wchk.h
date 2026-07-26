/* Rolling checksums over a byte stream, as a link-layer scanner would compute
 * them.
 *
 * The checksum of a window is the sum of its bytes modulo 65521, which is the
 * contract. Whether an implementation recomputes it or carries it forward is
 * not.
 */
#ifndef WCHK_H
#define WCHK_H

#include <stddef.h>
#include <stdint.h>

#define WCHK_MOD 65521u

/* Checksum of the `width` bytes starting at `at`. */
uint32_t wchk_window(const unsigned char *data, size_t len, size_t at, size_t width);

/* Checksums of every window of `width` bytes, written to `out`, which must hold
 * (len - width + 1) entries. Returns how many were written. */
size_t wchk_scan(const unsigned char *data, size_t len, size_t width, uint32_t *out);

/* Number of windows whose checksum is divisible by `divisor`. */
size_t wchk_count_divisible(const unsigned char *data, size_t len, size_t width,
                            uint32_t divisor);

#endif /* WCHK_H */
