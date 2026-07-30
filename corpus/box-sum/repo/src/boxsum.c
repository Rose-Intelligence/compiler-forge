/* Correct, and re-summing the rectangle on every query.
 *
 * The grid is fixed at build time. Each query walks its whole rectangle; a
 * summed-area table built once lets any rectangle be answered from four corners.
 */
#include "boxsum.h"

#include <stdlib.h>
#include <string.h>

#include "internal.h"

struct bs_grid {
    long *cells;
    size_t rows;
    size_t cols;
};

bs_grid *bs_build(const long *cells, size_t rows, size_t cols)
{
    bs_grid *g = calloc(1, sizeof *g);
    if (g == NULL) {
        return NULL;
    }
    size_t n = rows * cols;
    g->cells = calloc(n > 0 ? n : 1, sizeof *g->cells);
    if (g->cells == NULL) {
        free(g);
        return NULL;
    }
    memcpy(g->cells, cells, n * sizeof *cells);
    g->rows = rows;
    g->cols = cols;
    return g;
}

void bs_free(bs_grid *g)
{
    if (g == NULL) {
        return;
    }
    free(g->cells);
    free(g);
}

size_t bs_rows(const bs_grid *g) { return g == NULL ? 0 : g->rows; }
size_t bs_cols(const bs_grid *g) { return g == NULL ? 0 : g->cols; }

static int clamp_rect(const bs_grid *g, size_t *r0, size_t *c0, size_t *r1, size_t *c1)
{
    if (g == NULL || g->rows == 0 || g->cols == 0) {
        return 0;
    }
    if (*r0 >= g->rows || *c0 >= g->cols || *r0 > *r1 || *c0 > *c1) {
        return 0;
    }
    if (*r1 >= g->rows) {
        *r1 = g->rows - 1;
    }
    if (*c1 >= g->cols) {
        *c1 = g->cols - 1;
    }
    return 1;
}

long bs_box_sum(const bs_grid *g, size_t r0, size_t c0, size_t r1, size_t c1)
{
    if (!clamp_rect(g, &r0, &c0, &r1, &c1)) {
        return 0;
    }
    long acc = 0;
    for (size_t r = r0; r <= r1; r++) {
        for (size_t c = c0; c <= c1; c++) {
            acc = bs_add(acc, g->cells[r * g->cols + c]);
        }
    }
    return acc;
}

size_t bs_box_positive(const bs_grid *g, size_t r0, size_t c0, size_t r1, size_t c1)
{
    if (!clamp_rect(g, &r0, &c0, &r1, &c1)) {
        return 0;
    }
    size_t seen = 0;
    for (size_t r = r0; r <= r1; r++) {
        for (size_t c = c0; c <= c1; c++) {
            if (bs_positive(g->cells[r * g->cols + c])) {
                seen++;
            }
        }
    }
    return seen;
}
