#include "sparsedot.h"

#include <assert.h>
#include <stdio.h>

int main(void)
{
    long vec[] = {0, 3, 0, 0, -2, 0, 5};
    long q[]   = {1, 2, 3, 4,  5, 6, 7};
    sv_vec *v = sv_build(vec, 7);
    assert(v != NULL);
    assert(sv_size(v) == 7);
    /* dot = 3*2 + (-2)*5 + 5*7 = 6 - 10 + 35 = 31 */
    assert(sv_dot(v, q, 7) == 31);
    /* overlap: positions 1,4,6 nonzero in both */
    assert(sv_overlap(v, q, 7) == 3);

    long qz[] = {0, 0, 9, 9, 0, 9, 0};   /* zero where vec is nonzero */
    assert(sv_dot(v, qz, 7) == 0);
    assert(sv_overlap(v, qz, 7) == 0);

    sv_free(v);
    printf("ok\n");
    return 0;
}
