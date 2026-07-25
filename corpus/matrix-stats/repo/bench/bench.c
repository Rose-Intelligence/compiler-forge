/* Benchmark driver.
 *
 * The measured region is bracketed by CF_BENCH_START / CF_BENCH_STOP. Callgrind
 * runs with --instr-atstart=no, so process startup and input generation are not
 * counted. Without the bracket, a small kernel's real cost is swamped by process
 * setup, and work moved outside the region would measure as free.
 *
 * The digest is printed. The differential stage compares it between baseline and
 * candidate, so a patch that changes what the library computes is caught before
 * anything is measured.
 */
#include "mstats.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(__has_include)
#  if __has_include(<valgrind/callgrind.h>)
#    include <valgrind/callgrind.h>
#    define CF_BENCH_START CALLGRIND_START_INSTRUMENTATION
#    define CF_BENCH_STOP  CALLGRIND_STOP_INSTRUMENTATION
#  endif
#endif

#ifndef CF_BENCH_START
#  define CF_BENCH_START ((void)0)
#  define CF_BENCH_STOP  ((void)0)
#endif

static unsigned long next_random(unsigned long *state)
{
    *state = (*state * 6364136223846793005UL + 1442695040888963407UL);
    return (*state >> 33);
}

int main(int argc, char **argv)
{
    size_t rows = 400, cols = 40, repeats = 3;
    unsigned long seed = 0x2545F4914F6CDD1DUL;

    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--rows") == 0) rows = (size_t)strtoul(argv[i + 1], NULL, 10);
        else if (strcmp(argv[i], "--cols") == 0) cols = (size_t)strtoul(argv[i + 1], NULL, 10);
        else if (strcmp(argv[i], "--repeats") == 0) repeats = (size_t)strtoul(argv[i + 1], NULL, 10);
        else if (strcmp(argv[i], "--seed") == 0) seed = strtoul(argv[i + 1], NULL, 0);
    }

    ms_matrix *m = ms_matrix_alloc(rows, cols);
    if (m == NULL) {
        return 1;
    }
    unsigned long state = seed;
    for (size_t r = 0; r < rows; r++) {
        for (size_t c = 0; c < cols; c++) {
            ms_set(m, r, c, (double)(long)(next_random(&state) % 20000UL) / 100.0 - 100.0);
        }
    }

    double *scratch = malloc(cols * sizeof(double));
    if (scratch == NULL) {
        ms_matrix_free(m);
        return 1;
    }

    double checksum = 0.0;

    CF_BENCH_START;
    for (size_t iteration = 0; iteration < repeats; iteration++) {
        ms_col_means(m, scratch);
        checksum += scratch[0];
        ms_col_variances(m, scratch);
        checksum += scratch[cols - 1];
        checksum += ms_vec_max(m->data, rows * cols);
        ms_standardize(m);
        checksum += ms_digest(m);
    }
    CF_BENCH_STOP;

    printf("checksum %.9f\n", checksum);
    printf("rows %zu cols %zu repeats %zu\n", rows, cols, repeats);

    free(scratch);
    ms_matrix_free(m);
    return 0;
}
