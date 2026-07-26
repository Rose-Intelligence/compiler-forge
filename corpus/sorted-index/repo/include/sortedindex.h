/* A sorted string index with an opaque handle.
 *
 * The handle is opaque on purpose: the header promises the operations, never the
 * layout. An optimization is free to change how entries are stored as long as
 * every function below still answers the same way.
 */
#ifndef SORTEDINDEX_H
#define SORTEDINDEX_H

#include <stddef.h>

#define SI_NOT_FOUND ((size_t)-1)

typedef struct si_index si_index;

/* Build an index over `count` keys. Keys are copied. Returns NULL on
 * allocation failure. The keys are inserted in sorted order by the caller
 * contract below. */
si_index *si_build(const char *const *keys, size_t count);

void si_free(si_index *index);

/* Number of keys held. */
size_t si_size(const si_index *index);

/* Position of `key`, or SI_NOT_FOUND. Positions are the caller's original
 * ordering, which si_build requires to be sorted ascending. */
size_t si_lookup(const si_index *index, const char *key);

/* Number of keys with the given prefix. */
size_t si_count_prefix(const si_index *index, const char *prefix);

#endif /* SORTEDINDEX_H */
