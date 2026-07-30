/* A fixed, mostly-zero vector dotted against dense query vectors.
 *
 * The handle is opaque; an optimization may store only the nonzeros as long as
 * the queries answer identically.
 */
#ifndef SPARSEDOT_H
#define SPARSEDOT_H

#include <stddef.h>

typedef struct sv_vec sv_vec;

/* Build over `n` values (many expected to be zero). Values are copied. */
sv_vec *sv_build(const long *values, size_t n);
void sv_free(sv_vec *v);
size_t sv_size(const sv_vec *v);

/* Dot product with a dense query of the same length. */
long sv_dot(const sv_vec *v, const long *query, size_t n);

/* Count of positions where both this vector and the query are nonzero. */
size_t sv_overlap(const sv_vec *v, const long *query, size_t n);

#endif /* SPARSEDOT_H */
