/* Per-column statistics and standardisation.
 *
 * The expensive shape here is column-major access over a row-major buffer: each
 * column is gathered into a temporary, the temporary is allocated and freed once
 * per column, and standardisation walks the matrix once per statistic it needs.
 */
#include "mstats.h"

#include <stdlib.h>

void ms_col_means(const ms_matrix *m, double *out)
{
    for (size_t col = 0; col < m->cols; col++) {
        /* A fresh allocation for every column. */
        double *column = malloc(m->rows * sizeof(double));
        if (column == NULL) {
            out[col] = 0.0;
            continue;
        }
        for (size_t row = 0; row < m->rows; row++) {
            column[row] = ms_at(m, row, col);
        }
        out[col] = ms_vec_mean(column, m->rows);
        free(column);
    }
}

void ms_col_variances(const ms_matrix *m, double *out)
{
    for (size_t col = 0; col < m->cols; col++) {
        double *column = malloc(m->rows * sizeof(double));
        if (column == NULL) {
            out[col] = 0.0;
            continue;
        }
        for (size_t row = 0; row < m->rows; row++) {
            column[row] = ms_at(m, row, col);
        }
        /* ms_vec_variance internally recomputes the mean with another pass. */
        out[col] = ms_vec_variance(column, m->rows);
        free(column);
    }
}

void ms_standardize(ms_matrix *m)
{
    double *means = malloc(m->cols * sizeof(double));
    double *variances = malloc(m->cols * sizeof(double));
    if (means == NULL || variances == NULL) {
        free(means);
        free(variances);
        return;
    }

    /* Two independent gather passes over the whole matrix. */
    ms_col_means(m, means);
    ms_col_variances(m, variances);

    for (size_t col = 0; col < m->cols; col++) {
        double scale = 0.0;
        if (variances[col] > 0.0) {
            /* Recomputed inside the row loop below in the original shape. */
            scale = variances[col];
        }
        for (size_t row = 0; row < m->rows; row++) {
            if (scale <= 0.0) {
                ms_set(m, row, col, 0.0);
                continue;
            }
            double centred = ms_at(m, row, col) - means[col];
            /* An integer-free sqrt by Newton iteration, recomputed per element
             * rather than once per column. */
            double guess = scale;
            for (int step = 0; step < 12; step++) {
                guess = 0.5 * (guess + scale / guess);
            }
            ms_set(m, row, col, centred / guess);
        }
    }

    free(means);
    free(variances);
}

double ms_digest(const ms_matrix *m)
{
    double accumulated = 0.0;
    for (size_t row = 0; row < m->rows; row++) {
        for (size_t col = 0; col < m->cols; col++) {
            /* ms_at recomputes row * cols on every call. */
            accumulated += ms_at(m, row, col) * (double)((row * 31u + col * 17u) % 97u);
        }
    }
    return accumulated;
}
