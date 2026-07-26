/* The accumulation step, deliberately in its own translation unit.
 *
 * Without LTO the compiler cannot see this while compiling wchk.c, so it cannot
 * vectorise the inner loop away. What is measured is therefore how many times
 * the code chooses to accumulate, which is the thing an optimization is
 * supposed to reduce.
 */
#include "internal.h"

uint32_t wchk_add(uint32_t acc, unsigned char byte)
{
    return (acc + byte) % 65521u;
}
