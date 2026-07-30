/* Unit tests: the invariants a patch must not break, checked directly. */
#include "rangesum.h"

#include <assert.h>
#include <stdio.h>

int main(void)
{
    long v[] = {5, -2, 4, 7, 0, 3};   /* evens: -2, 4, 0 */
    rs_table *t = rs_build(v, 6);
    assert(t != NULL);
    assert(rs_size(t) == 6);

    assert(rs_range_sum(t, 0, 5) == 17);
    assert(rs_range_sum(t, 1, 3) == 9);
    assert(rs_range_sum(t, 2, 2) == 4);
    assert(rs_range_even(t, 0, 5) == 3);
    assert(rs_range_even(t, 0, 1) == 1);

    /* Bounds: hi clamps to the last index, inverted range is empty. */
    assert(rs_range_sum(t, 4, 100) == 3);
    assert(rs_range_sum(t, 3, 1) == 0);
    assert(rs_range_even(t, 3, 1) == 0);

    rs_free(t);
    printf("ok\n");
    return 0;
}
