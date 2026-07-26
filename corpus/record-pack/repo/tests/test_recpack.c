#include "recpack.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void)
{
    rp_record recs[] = {
        {"a", "1"},
        {"key", "value"},
        {"", ""},
        {"k", ""},
    };
    const size_t n = sizeof recs / sizeof recs[0];

    /* 2 + 1 + 1 | 2 + 3 + 5 | 2 | 2 + 1 */
    assert(rp_packed_size(recs, n) == 4 + 10 + 2 + 3);

    size_t len = 0;
    unsigned char *packed = rp_pack(recs, n, &len);
    assert(packed != NULL);
    assert(len == rp_packed_size(recs, n));
    assert(rp_count(packed, len) == n);

    /* Exact bytes for the first record. */
    assert(packed[0] == 1 && packed[1] == 'a');
    assert(packed[2] == 1 && packed[3] == '1');
    free(packed);

    /* An empty record set still returns a valid, freeable buffer. */
    packed = rp_pack(recs, 0, &len);
    assert(packed != NULL);
    assert(len == 0);
    assert(rp_count(packed, 0) == 0);
    free(packed);

    /* A field longer than the length prefix can hold is refused, not truncated. */
    char big[300];
    memset(big, 'x', sizeof big - 1);
    big[sizeof big - 1] = '\0';
    rp_record oversized[] = {{big, "v"}};
    assert(rp_pack(oversized, 1, &len) == NULL);

    assert(rp_packed_size(NULL, 3) == 0);
    assert(rp_pack(NULL, 3, &len) == NULL);

    printf("ok\n");
    return 0;
}
