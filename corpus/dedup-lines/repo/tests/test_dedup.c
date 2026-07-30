#include "dedup.h"

#include <assert.h>
#include <stdio.h>

int main(void)
{
    const char *a[] = {"x", "y", "x", "z", "y", "x"};   /* distinct: x,y,z */
    unsigned long h1 = 0;
    assert(dl_run(a, 6, &h1) == 3);

    const char *b[] = {"x", "z", "y"};   /* same set, different order */
    unsigned long h2 = 0;
    assert(dl_run(b, 3, &h2) == 3);
    assert(h1 == h2);   /* hashsum is order-independent */

    const char *c[] = {"q", "q", "q"};
    unsigned long h3 = 0;
    assert(dl_run(c, 3, &h3) == 1);

    assert(dl_run(NULL, 0, NULL) == 0);

    printf("ok\n");
    return 0;
}
