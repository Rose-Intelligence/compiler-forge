/* Correct, and it recomputes every window from scratch.
 *
 * Consecutive windows overlap in all but two bytes. Nothing here uses that:
 * each window is summed from its first byte, so scanning a buffer costs
 * O(len * width) where O(len) was available.
 */
#include "wchk.h"

#include "internal.h"

uint32_t wchk_window(const unsigned char *data, size_t len, size_t at, size_t width)
{
    if (data == NULL || at + width > len) {
        return 0;
    }

    uint32_t acc = 0;
    for (size_t i = 0; i < width; i++) {
        acc = wchk_add(acc, data[at + i]);
    }
    return acc;
}

size_t wchk_scan(const unsigned char *data, size_t len, size_t width, uint32_t *out)
{
    if (data == NULL || out == NULL || width == 0 || width > len) {
        return 0;
    }

    const size_t windows = len - width + 1;
    for (size_t w = 0; w < windows; w++) {
        /* Each window is summed from scratch, re-reading width-2 bytes that the
         * previous window already accounted for. */
        out[w] = wchk_window(data, len, w, width);
    }
    return windows;
}

size_t wchk_count_divisible(const unsigned char *data, size_t len, size_t width,
                            uint32_t divisor)
{
    if (data == NULL || width == 0 || width > len || divisor == 0) {
        return 0;
    }

    const size_t windows = len - width + 1;
    size_t seen = 0;
    for (size_t w = 0; w < windows; w++) {
        if (wchk_window(data, len, w, width) % divisor == 0) {
            seen++;
        }
    }
    return seen;
}
