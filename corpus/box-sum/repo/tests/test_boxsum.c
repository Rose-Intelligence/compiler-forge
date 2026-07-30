#include "boxsum.h"

#include <assert.h>
#include <stdio.h>

int main(void)
{
    /* 3x3:
     *  1 -2  3
     *  4  0 -1
     * -5  6  2   */
    long cells[] = {1, -2, 3, 4, 0, -1, -5, 6, 2};
    bs_grid *g = bs_build(cells, 3, 3);
    assert(g != NULL);
    assert(bs_rows(g) == 3 && bs_cols(g) == 3);

    assert(bs_box_sum(g, 0, 0, 2, 2) == 8);     /* whole grid */
    assert(bs_box_sum(g, 0, 0, 0, 2) == 2);     /* top row */
    assert(bs_box_sum(g, 1, 1, 2, 2) == 7);     /* bottom-right 2x2: 0-1+6+2 */
    assert(bs_box_positive(g, 0, 0, 2, 2) == 5); /* 1,3,4,6,2 */
    assert(bs_box_positive(g, 1, 1, 1, 1) == 0); /* single 0 */

    assert(bs_box_sum(g, 0, 0, 100, 100) == 8);  /* clamps */
    assert(bs_box_sum(g, 2, 2, 0, 0) == 0);      /* inverted */

    bs_free(g);
    printf("ok\n");
    return 0;
}
