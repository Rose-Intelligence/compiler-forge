/* A range-query table over a fixed sequence of integers.
 *
 * The handle is opaque: the header promises the queries, never the storage. An
 * optimization may keep whatever auxiliary arrays it likes as long as every
 * function below still answers identically.
 */
#ifndef RANGESUM_H
#define RANGESUM_H

#include <stddef.h>

typedef struct rs_table rs_table;

/* Build a table over `count` values. Values are copied. NULL on allocation
 * failure. */
rs_table *rs_build(const long *values, size_t count);

void rs_free(rs_table *t);

/* Number of values held. */
size_t rs_size(const rs_table *t);

/* Sum of values in the inclusive index range [lo, hi]. hi is clamped to the
 * last index; an empty or inverted range sums to 0. */
long rs_range_sum(const rs_table *t, size_t lo, size_t hi);

/* Count of even values in the inclusive index range [lo, hi], same bounds
 * rules as rs_range_sum. */
size_t rs_range_even(const rs_table *t, size_t lo, size_t hi);

#endif /* RANGESUM_H */
