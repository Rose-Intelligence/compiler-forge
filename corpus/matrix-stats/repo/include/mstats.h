/* mstats — descriptive statistics over dense row-major matrices.
 *
 * Public API. This header is validator-owned: a candidate may not change any
 * declaration here, because callers outside the package compile against it.
 */
#ifndef MSTATS_H
#define MSTATS_H

#include <stddef.h>

typedef struct {
    size_t rows;
    size_t cols;
    double *data; /* row-major, rows*cols elements */
} ms_matrix;

/* Allocation. Returns NULL on failure; ms_matrix_free tolerates NULL. */
ms_matrix *ms_matrix_alloc(size_t rows, size_t cols);
void ms_matrix_free(ms_matrix *m);

/* Element access. Out-of-range indices are undefined behaviour by contract. */
double ms_at(const ms_matrix *m, size_t row, size_t col);
void ms_set(ms_matrix *m, size_t row, size_t col, double value);

/* Vector reductions over a contiguous span. */
double ms_vec_sum(const double *v, size_t n);
double ms_vec_mean(const double *v, size_t n);
double ms_vec_variance(const double *v, size_t n);
double ms_vec_max(const double *v, size_t n);

/* Per-column statistics. Each writes `cols` doubles into `out`. */
void ms_col_means(const ms_matrix *m, double *out);
void ms_col_variances(const ms_matrix *m, double *out);

/* Standardises every column in place to zero mean and unit variance.
 * Columns with zero variance are left as zeroes. */
void ms_standardize(ms_matrix *m);

/* A single summary number over the whole matrix, used as a checksum. */
double ms_digest(const ms_matrix *m);

#endif /* MSTATS_H */
