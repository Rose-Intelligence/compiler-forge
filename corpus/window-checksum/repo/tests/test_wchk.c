#include "wchk.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void)
{
    const unsigned char data[] = {1, 2, 3, 4, 5, 6, 7, 8};
    const size_t len = sizeof data;

    /* A single window is just the sum of its bytes. */
    assert(wchk_window(data, len, 0, 3) == 6);
    assert(wchk_window(data, len, 5, 3) == 21);
    assert(wchk_window(data, len, 0, len) == 36);

    /* Out of range is defined, not undefined. */
    assert(wchk_window(data, len, 6, 3) == 0);
    assert(wchk_window(NULL, len, 0, 3) == 0);

    uint32_t out[8];
    size_t n = wchk_scan(data, len, 3, out);
    assert(n == len - 3 + 1);
    for (size_t i = 0; i < n; i++) {
        assert(out[i] == wchk_window(data, len, i, 3));
    }

    /* A window as wide as the buffer yields exactly one result. */
    assert(wchk_scan(data, len, len, out) == 1);
    /* Wider than the buffer yields none. */
    assert(wchk_scan(data, len, len + 1, out) == 0);
    assert(wchk_scan(data, len, 0, out) == 0);

    assert(wchk_count_divisible(data, len, 3, 3) == 2);  /* 6 and 21 */
    assert(wchk_count_divisible(data, len, 3, 1) == n);
    assert(wchk_count_divisible(data, len, 3, 0) == 0);

    /* The modulus is observable: a window summing past it must wrap. */
    unsigned char *big = malloc(300);
    assert(big != NULL);
    memset(big, 255, 300);
    assert(wchk_window(big, 300, 0, 300) == (300u * 255u) % WCHK_MOD);
    free(big);

    printf("ok\n");
    return 0;
}
