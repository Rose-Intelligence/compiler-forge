/* Internal helpers. Not part of the public contract, so an optimization may
 * change or remove them; si_lookup and si_count_prefix must still answer the
 * same way. */
#ifndef SORTEDINDEX_INTERNAL_H
#define SORTEDINDEX_INTERNAL_H

int si_key_cmp(const char *a, const char *b);
int si_has_prefix(const char *key, const char *prefix);

#endif /* SORTEDINDEX_INTERNAL_H */
