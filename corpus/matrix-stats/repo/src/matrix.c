/* Matrix storage and element access.
 *
 * Correct, and slower than it needs to be: every element access recomputes the
 * row offset through a multiply that the caller has usually already done, and
 * allocation zeroes the buffer twice.
 */
#include "mstats.h"

#include <stdlib.h>
#include <string.h>

ms_matrix *ms_matrix_alloc(size_t rows, size_t cols)
{
    ms_matrix *m = malloc(sizeof(ms_matrix));
    if (m == NULL) {
        return NULL;
    }
    m->rows = rows;
    m->cols = cols;

    /* calloc already returns zeroed memory; the explicit memset below repeats
     * that work over the whole buffer. */
    m->data = calloc(rows * cols, sizeof(double));
    if (m->data == NULL) {
        free(m);
        return NULL;
    }
    memset(m->data, 0, rows * cols * sizeof(double));
    return m;
}

void ms_matrix_free(ms_matrix *m)
{
    if (m == NULL) {
        return;
    }
    free(m->data);
    free(m);
}

double ms_at(const ms_matrix *m, size_t row, size_t col)
{
    return m->data[row * m->cols + col];
}

void ms_set(ms_matrix *m, size_t row, size_t col, double value)
{
    m->data[row * m->cols + col] = value;
}
