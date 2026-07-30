/* Multiply-accumulate in its own translation unit, so the dense scan over every
 * component (zeros included) stays real under the no-LTO toolchain. */
#include "internal.h"

long sv_madd(long acc, long a, long b)
{
    return acc + a * b;
}
