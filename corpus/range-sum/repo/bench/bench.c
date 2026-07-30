/* Benchmark driver.
 *
 * The measured region is bracketed by CF_BENCH_START / CF_BENCH_STOP: Callgrind
 * runs with --instr-atstart=no, so building the table is not counted and work
 * moved out of the region would measure as free. The two checksums are printed;
 * the differential stage compares them so a patch that changes an answer is
 * caught before anything is measured.
 */
#include "rangesum.h"

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

static unsigned xs(unsigned *state)
{
    unsigned x = *state;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    *state = x;
    return x;
}

int main(int argc, char **argv)
{
    size_t values = 48;
    size_t queries = 400;

    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--values") == 0) {
            values = (size_t)strtoul(argv[i + 1], NULL, 10);
        } else if (strcmp(argv[i], "--queries") == 0) {
            queries = (size_t)strtoul(argv[i + 1], NULL, 10);
        }
    }
    if (values == 0) {
        values = 1;
    }

    long *data = calloc(values, sizeof *data);
    if (data == NULL) {
        return 1;
    }
    unsigned state = 2463534242u;
    for (size_t i = 0; i < values; i++) {
        data[i] = (long)(xs(&state) % 1000) - 500;
    }

    rs_table *t = rs_build(data, values);
    if (t == NULL) {
        return 1;
    }

    long sum_check = 0;
    size_t even_check = 0;

    CF_BENCH_START;
    for (size_t q = 0; q < queries; q++) {
        size_t a = xs(&state) % values;
        size_t b = xs(&state) % values;
        size_t lo = a < b ? a : b;
        size_t hi = a < b ? b : a;
        sum_check += rs_range_sum(t, lo, hi);
        even_check += rs_range_even(t, lo, hi);
    }
    CF_BENCH_STOP;

    printf("%zu %ld %zu\n", rs_size(t), sum_check, even_check);

    rs_free(t);
    free(data);
    return 0;
}
