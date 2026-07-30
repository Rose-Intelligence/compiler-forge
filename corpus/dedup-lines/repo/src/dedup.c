/* Correct, and quadratic: each line is compared against every earlier line to
 * decide whether it is new. Sorting the batch once collapses those comparisons
 * to a single adjacent-pair walk, but nothing here orders the data.
 */
#include "dedup.h"

#include "internal.h"

size_t dl_run(const char *const *lines, size_t n, unsigned long *hashsum)
{
    size_t distinct = 0;
    unsigned long sum = 0;

    for (size_t i = 0; i < n; i++) {
        int seen = 0;
        for (size_t j = 0; j < i; j++) {   /* scans every earlier line */
            if (dl_eq(lines[i], lines[j])) {
                seen = 1;
                break;
            }
        }
        if (!seen) {
            distinct++;
            sum += dl_hash(lines[i]);
        }
    }

    if (hashsum != NULL) {
        *hashsum = sum;
    }
    return distinct;
}
