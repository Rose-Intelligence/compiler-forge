#include "boxsum.h"

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
    size_t side = 10;
    size_t queries = 400;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--side") == 0) {
            side = (size_t)strtoul(argv[i + 1], NULL, 10);
        } else if (strcmp(argv[i], "--queries") == 0) {
            queries = (size_t)strtoul(argv[i + 1], NULL, 10);
        }
    }
    if (side == 0) {
        side = 1;
    }

    long *cells = calloc(side * side, sizeof *cells);
    if (cells == NULL) {
        return 1;
    }
    unsigned state = 2463534242u;
    for (size_t i = 0; i < side * side; i++) {
        cells[i] = (long)(xs(&state) % 200) - 100;
    }

    bs_grid *g = bs_build(cells, side, side);
    if (g == NULL) {
        return 1;
    }

    long sum_check = 0;
    size_t pos_check = 0;
    CF_BENCH_START;
    for (size_t q = 0; q < queries; q++) {
        size_t r0 = xs(&state) % side, r1 = xs(&state) % side;
        size_t c0 = xs(&state) % side, c1 = xs(&state) % side;
        if (r0 > r1) { size_t t = r0; r0 = r1; r1 = t; }
        if (c0 > c1) { size_t t = c0; c0 = c1; c1 = t; }
        sum_check += bs_box_sum(g, r0, c0, r1, c1);
        pos_check += bs_box_positive(g, r0, c0, r1, c1);
    }
    CF_BENCH_STOP;

    printf("%zu %ld %zu\n", bs_rows(g) * bs_cols(g), sum_check, pos_check);
    bs_free(g);
    free(cells);
    return 0;
}
