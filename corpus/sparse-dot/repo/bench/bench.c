#include "sparsedot.h"

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
    size_t size = 200;
    size_t density = 12;   /* percent nonzero */
    size_t queries = 300;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--size") == 0) size = (size_t)strtoul(argv[i + 1], NULL, 10);
        else if (strcmp(argv[i], "--density") == 0) density = (size_t)strtoul(argv[i + 1], NULL, 10);
        else if (strcmp(argv[i], "--queries") == 0) queries = (size_t)strtoul(argv[i + 1], NULL, 10);
    }
    if (size == 0) size = 1;

    long *vec = calloc(size, sizeof *vec);
    long *q = calloc(size, sizeof *q);
    if (vec == NULL || q == NULL) return 1;
    unsigned state = 2463534242u;
    for (size_t i = 0; i < size; i++) {
        vec[i] = (xs(&state) % 100 < density) ? (long)(xs(&state) % 19) - 9 : 0;
        if (vec[i] == 0 && (xs(&state) % 100 < density)) vec[i] = 1;   /* ensure some nonzeros */
        q[i] = (long)(xs(&state) % 7) - 3;   /* dense query */
    }

    sv_vec *v = sv_build(vec, size);
    if (v == NULL) return 1;

    long dot_check = 0;
    size_t ov_check = 0;
    CF_BENCH_START;
    for (size_t k = 0; k < queries; k++) {
        dot_check += sv_dot(v, q, size);
        ov_check += sv_overlap(v, q, size);
    }
    CF_BENCH_STOP;

    printf("%zu %ld %zu\n", sv_size(v), dot_check, ov_check);
    sv_free(v);
    free(vec); free(q);
    return 0;
}
