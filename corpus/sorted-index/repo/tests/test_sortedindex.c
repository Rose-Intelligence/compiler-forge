/* The package's own tests. A patch must leave every one of these passing. */
#include "sortedindex.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(void)
{
    const char *keys[] = {"alpha", "alpine", "beta", "delta", "delta9", "zulu"};
    const size_t n = sizeof keys / sizeof keys[0];

    si_index *ix = si_build(keys, n);
    assert(ix != NULL);
    assert(si_size(ix) == n);

    /* Every key resolves to its own position. */
    for (size_t i = 0; i < n; i++) {
        assert(si_lookup(ix, keys[i]) == i);
    }

    /* Absent keys, including ones that sort before, inside and after. */
    assert(si_lookup(ix, "aardvark") == SI_NOT_FOUND);
    assert(si_lookup(ix, "carrot") == SI_NOT_FOUND);
    assert(si_lookup(ix, "zzz") == SI_NOT_FOUND);
    assert(si_lookup(ix, "") == SI_NOT_FOUND);

    /* Prefix counts, including a prefix that is itself a key and one that
     * matches nothing. */
    assert(si_count_prefix(ix, "al") == 2);
    assert(si_count_prefix(ix, "delta") == 2);
    assert(si_count_prefix(ix, "z") == 1);
    assert(si_count_prefix(ix, "q") == 0);
    /* The empty prefix matches everything. */
    assert(si_count_prefix(ix, "") == n);

    si_free(ix);

    /* An empty index is well defined. */
    si_index *empty = si_build(keys, 0);
    assert(empty != NULL);
    assert(si_size(empty) == 0);
    assert(si_lookup(empty, "alpha") == SI_NOT_FOUND);
    assert(si_count_prefix(empty, "") == 0);
    si_free(empty);

    /* NULL is tolerated rather than crashing. */
    assert(si_size(NULL) == 0);
    assert(si_lookup(NULL, "x") == SI_NOT_FOUND);
    assert(si_count_prefix(NULL, "x") == 0);
    si_free(NULL);

    printf("ok\n");
    return 0;
}
