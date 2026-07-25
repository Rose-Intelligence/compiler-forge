/* Differential harness. Reads one case on stdin and prints every observable the
 * equivalence contract declares, so baseline and candidate can be compared on
 * inputs the miner never saw.
 *
 * Case format, one line: <rows> <cols> <seed>
 */
#include "mstats.h"

#include <stdio.h>
#include <stdlib.h>

static unsigned long next_random(unsigned long *state)
{
    *state = (*state * 6364136223846793005UL + 1442695040888963407UL);
    return (*state >> 33);
}

static void fill(ms_matrix *m, unsigned long seed)
{
    unsigned long state = seed;
    for (size_t r = 0; r < m->rows; r++) {
        for (size_t c = 0; c < m->cols; c++) {
            /* Quantised so the values are exactly representable and the
             * comparison does not depend on rounding mode. */
            const double value = (double)(long)(next_random(&state) % 20000UL) / 100.0 - 100.0;
            ms_set(m, r, c, value);
        }
    }
}

int main(void)
{
    unsigned long rows = 0, cols = 0, seed = 0;
    if (scanf("%lu %lu %lu", &rows, &cols, &seed) != 3) {
        fprintf(stderr, "bad case\n");
        return 2;
    }
    if (rows == 0 || cols == 0 || rows > 4096 || cols > 512) {
        fprintf(stderr, "case out of range\n");
        return 2;
    }

    ms_matrix *m = ms_matrix_alloc(rows, cols);
    if (m == NULL) {
        return 3;
    }
    fill(m, seed);

    double *means = malloc(cols * sizeof(double));
    double *variances = malloc(cols * sizeof(double));
    if (means == NULL || variances == NULL) {
        free(means);
        free(variances);
        ms_matrix_free(m);
        return 3;
    }

    ms_col_means(m, means);
    ms_col_variances(m, variances);

    printf("means");
    for (size_t c = 0; c < cols; c++) {
        printf(" %.9f", means[c]);
    }
    printf("\nvars");
    for (size_t c = 0; c < cols; c++) {
        printf(" %.9f", variances[c]);
    }
    printf("\nmax %.9f\n", ms_vec_max(m->data, rows * cols));

    ms_standardize(m);
    printf("digest %.9f\n", ms_digest(m));

    free(means);
    free(variances);
    ms_matrix_free(m);
    return 0;
}
