/* Run detection, deliberately in its own translation unit.
 *
 * The pinned toolchain builds without LTO, so the compiler cannot see this
 * while compiling rle.c. That keeps the length recomputation in the encoder
 * genuinely expensive instead of something the optimizer quietly removes, and
 * it means the measurement reflects the algorithm rather than the inliner.
 */
#include "internal.h"

size_t rle_run_length(const char *s, size_t at, size_t len)
{
    size_t run = 1;
    while (at + run < len && s[at + run] == s[at] && run < 255) {
        run++;
    }
    return run;
}
