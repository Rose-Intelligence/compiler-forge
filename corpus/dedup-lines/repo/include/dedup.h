/* Distinct-line counting over a batch of lines.
 *
 * The result is a count and an order-independent hash of the distinct set, so an
 * optimization may reorder its work freely as long as both answers match.
 */
#ifndef DEDUP_H
#define DEDUP_H

#include <stddef.h>

/* Number of distinct strings among lines[0..n). *hashsum receives the sum of a
 * per-line hash over the distinct set (order-independent). */
size_t dl_run(const char *const *lines, size_t n, unsigned long *hashsum);

#endif /* DEDUP_H */
