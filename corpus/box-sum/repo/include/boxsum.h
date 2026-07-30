/* A 2-D grid queried for rectangular region sums.
 *
 * The handle is opaque; an optimization may keep any auxiliary table as long as
 * the region queries answer identically.
 */
#ifndef BOXSUM_H
#define BOXSUM_H

#include <stddef.h>

typedef struct bs_grid bs_grid;

/* Build over a row-major `rows` x `cols` grid. Cells are copied. */
bs_grid *bs_build(const long *cells, size_t rows, size_t cols);
void bs_free(bs_grid *g);
size_t bs_rows(const bs_grid *g);
size_t bs_cols(const bs_grid *g);

/* Sum of cells in the inclusive rectangle [r0..r1] x [c0..c1]. r1/c1 clamp to the
 * last row/col; an empty or inverted rectangle sums to 0. */
long bs_box_sum(const bs_grid *g, size_t r0, size_t c0, size_t r1, size_t c1);

/* Count of strictly-positive cells in the same rectangle. */
size_t bs_box_positive(const bs_grid *g, size_t r0, size_t c0, size_t r1, size_t c1);

#endif /* BOXSUM_H */
