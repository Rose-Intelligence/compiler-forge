/* Per-cell step in its own translation unit, so the region scan stays a genuine
 * area walk under the no-LTO toolchain rather than a vectorised blur. */
#include "internal.h"

long bs_add(long acc, long v)
{
    return acc + v;
}

int bs_positive(long v)
{
    return v > 0;
}
