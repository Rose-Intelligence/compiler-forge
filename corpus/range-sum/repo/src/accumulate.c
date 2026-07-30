/* The per-element step, kept in its own translation unit.
 *
 * The pinned toolchain builds without LTO, so the compiler cannot see these
 * definitions while compiling rangesum.c. That is what keeps the per-query scan
 * genuinely linear in the range: an optimizer that could inline them might
 * vectorise the loop and blur the algorithmic cost being measured.
 */
#include "internal.h"

long rs_add(long acc, long value)
{
    return acc + value;
}

int rs_is_even(long value)
{
    return (value & 1L) == 0;
}
