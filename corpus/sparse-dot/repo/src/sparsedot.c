/* Correct, and touching every component including the zeros.
 *
 * The vector is fixed and mostly zero. Both queries walk all n components; the
 * zeros contribute nothing but are visited anyway. Recording the nonzero
 * positions once makes each query cost the number of nonzeros, not n.
 */
#include "sparsedot.h"

#include <stdlib.h>
#include <string.h>

#include "internal.h"

struct sv_vec {
    long *values;
    size_t n;
};

sv_vec *sv_build(const long *values, size_t n)
{
    sv_vec *v = calloc(1, sizeof *v);
    if (v == NULL) {
        return NULL;
    }
    v->values = calloc(n > 0 ? n : 1, sizeof *v->values);
    if (v->values == NULL) {
        free(v);
        return NULL;
    }
    memcpy(v->values, values, n * sizeof *values);
    v->n = n;
    return v;
}

void sv_free(sv_vec *v)
{
    if (v == NULL) {
        return;
    }
    free(v->values);
    free(v);
}

size_t sv_size(const sv_vec *v) { return v == NULL ? 0 : v->n; }

long sv_dot(const sv_vec *v, const long *query, size_t n)
{
    if (v == NULL || query == NULL) {
        return 0;
    }
    size_t m = n < v->n ? n : v->n;
    long acc = 0;
    for (size_t i = 0; i < m; i++) {   /* visits zeros too */
        acc = sv_madd(acc, v->values[i], query[i]);
    }
    return acc;
}

size_t sv_overlap(const sv_vec *v, const long *query, size_t n)
{
    if (v == NULL || query == NULL) {
        return 0;
    }
    size_t m = n < v->n ? n : v->n;
    size_t seen = 0;
    for (size_t i = 0; i < m; i++) {
        if (v->values[i] != 0 && query[i] != 0) {
            seen++;
        }
    }
    return seen;
}
