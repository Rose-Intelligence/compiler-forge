/* Correct, and asymptotically wrong.
 *
 * si_build requires its input sorted, and every entry stays in that order for
 * the life of the index. Both queries below ignore that and walk the whole
 * array. The information needed to do better is already present; nothing here
 * uses it.
 */
#include "sortedindex.h"

#include <stdlib.h>
#include <string.h>

#include "internal.h"

struct si_index {
    char **keys;
    size_t count;
};

si_index *si_build(const char *const *keys, size_t count)
{
    si_index *index = calloc(1, sizeof *index);
    if (index == NULL) {
        return NULL;
    }
    index->keys = calloc(count > 0 ? count : 1, sizeof *index->keys);
    if (index->keys == NULL) {
        free(index);
        return NULL;
    }
    for (size_t i = 0; i < count; i++) {
        size_t len = strlen(keys[i]);
        index->keys[i] = malloc(len + 1);
        if (index->keys[i] == NULL) {
            for (size_t j = 0; j < i; j++) {
                free(index->keys[j]);
            }
            free(index->keys);
            free(index);
            return NULL;
        }
        memcpy(index->keys[i], keys[i], len + 1);
    }
    index->count = count;
    return index;
}

void si_free(si_index *index)
{
    if (index == NULL) {
        return;
    }
    for (size_t i = 0; i < index->count; i++) {
        free(index->keys[i]);
    }
    free(index->keys);
    free(index);
}

size_t si_size(const si_index *index)
{
    return index == NULL ? 0 : index->count;
}

size_t si_lookup(const si_index *index, const char *key)
{
    if (index == NULL || key == NULL) {
        return SI_NOT_FOUND;
    }

    /* The keys are sorted and this walks all of them anyway. */
    for (size_t i = 0; i < index->count; i++) {
        if (si_key_cmp(index->keys[i], key) == 0) {
            return i;
        }
    }
    return SI_NOT_FOUND;
}

size_t si_count_prefix(const si_index *index, const char *prefix)
{
    if (index == NULL || prefix == NULL) {
        return 0;
    }

    /* Matches occupy one contiguous run because the keys are sorted. This scans
     * past the end of that run to the end of the array regardless. */
    size_t seen = 0;
    for (size_t i = 0; i < index->count; i++) {
        if (si_has_prefix(index->keys[i], prefix)) {
            seen++;
        }
    }
    return seen;
}
