/* Correct, and doing linear work a table should answer in constant time.
 *
 * Every value is known at build time and never changes. Both queries below
 * re-walk the requested range on every call; the information to answer either in
 * O(1) — a running total carried once across the sequence — is never formed.
 */
#include "rangesum.h"

#include <stdlib.h>
#include <string.h>

#include "internal.h"

struct rs_table {
    long *values;
    size_t count;
};

rs_table *rs_build(const long *values, size_t count)
{
    rs_table *t = calloc(1, sizeof *t);
    if (t == NULL) {
        return NULL;
    }
    t->values = calloc(count > 0 ? count : 1, sizeof *t->values);
    if (t->values == NULL) {
        free(t);
        return NULL;
    }
    memcpy(t->values, values, count * sizeof *values);
    t->count = count;
    return t;
}

void rs_free(rs_table *t)
{
    if (t == NULL) {
        return;
    }
    free(t->values);
    free(t);
}

size_t rs_size(const rs_table *t)
{
    return t == NULL ? 0 : t->count;
}

long rs_range_sum(const rs_table *t, size_t lo, size_t hi)
{
    if (t == NULL || t->count == 0 || lo >= t->count || lo > hi) {
        return 0;
    }
    if (hi >= t->count) {
        hi = t->count - 1;
    }

    /* Walks the whole range every call, though nothing in it ever changes. */
    long acc = 0;
    for (size_t i = lo; i <= hi; i++) {
        acc = rs_add(acc, t->values[i]);
    }
    return acc;
}

size_t rs_range_even(const rs_table *t, size_t lo, size_t hi)
{
    if (t == NULL || t->count == 0 || lo >= t->count || lo > hi) {
        return 0;
    }
    if (hi >= t->count) {
        hi = t->count - 1;
    }

    size_t seen = 0;
    for (size_t i = lo; i <= hi; i++) {
        if (rs_is_even(t->values[i])) {
            seen++;
        }
    }
    return seen;
}
