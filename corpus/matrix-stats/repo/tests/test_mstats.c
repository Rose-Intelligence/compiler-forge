/* Unit tests. Validator-owned: a candidate may not weaken or delete these. */
#include "mstats.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>

static int failures = 0;

static void check(int condition, const char *what)
{
    if (!condition) {
        printf("FAIL %s\n", what);
        failures++;
    }
}

static void check_close(double a, double b, const char *what)
{
    check(fabs(a - b) < 1e-9, what);
}

int main(void)
{
    const double v[] = {2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0};
    const size_t n = sizeof(v) / sizeof(v[0]);

    check_close(ms_vec_sum(v, n), 40.0, "sum");
    check_close(ms_vec_mean(v, n), 5.0, "mean");
    check_close(ms_vec_variance(v, n), 4.0, "variance");
    check_close(ms_vec_max(v, n), 9.0, "max");
    check_close(ms_vec_sum(v, 0), 0.0, "sum of empty");
    check_close(ms_vec_mean(v, 0), 0.0, "mean of empty");

    ms_matrix *m = ms_matrix_alloc(4, 3);
    check(m != NULL, "alloc");
    if (m == NULL) {
        return 1;
    }
    check_close(ms_at(m, 2, 1), 0.0, "alloc zeroes");

    for (size_t r = 0; r < 4; r++) {
        for (size_t c = 0; c < 3; c++) {
            ms_set(m, r, c, (double)(r * 3 + c));
        }
    }
    check_close(ms_at(m, 3, 2), 11.0, "set/at round trip");

    double means[3];
    ms_col_means(m, means);
    check_close(means[0], 4.5, "column mean 0");
    check_close(means[2], 6.5, "column mean 2");

    double variances[3];
    ms_col_variances(m, variances);
    check_close(variances[1], 45.0 / 4.0, "column variance 1");

    ms_standardize(m);
    double after[3];
    ms_col_means(m, after);
    check_close(after[0], 0.0, "standardised mean is zero");
    check_close(ms_vec_variance(m->data, 0), 0.0, "degenerate variance");

    /* A zero-variance column must standardise to zeroes, not to NaN. */
    ms_matrix *flat = ms_matrix_alloc(3, 1);
    if (flat != NULL) {
        for (size_t r = 0; r < 3; r++) {
            ms_set(flat, r, 0, 7.0);
        }
        ms_standardize(flat);
        check_close(ms_at(flat, 1, 0), 0.0, "zero-variance column");
        ms_matrix_free(flat);
    }

    ms_matrix_free(m);
    ms_matrix_free(NULL);

    if (failures == 0) {
        printf("ok\n");
    }
    return failures == 0 ? 0 : 1;
}
