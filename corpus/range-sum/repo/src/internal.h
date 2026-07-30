/* Internal helpers, deliberately in their own translation unit. Not part of the
 * public contract, so an optimization may change or remove them; the queries
 * must still answer the same way. */
#ifndef RANGESUM_INTERNAL_H
#define RANGESUM_INTERNAL_H

long rs_add(long acc, long value);
int rs_is_even(long value);

#endif /* RANGESUM_INTERNAL_H */
