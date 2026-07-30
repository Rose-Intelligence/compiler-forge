/* Benchmark driver. The dedup pass is the measured region; generating the batch
 * is not. */
#include "dedup.h"

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
    size_t lines = 40;
    size_t repeats = 200;

    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--lines") == 0) {
            lines = (size_t)strtoul(argv[i + 1], NULL, 10);
        } else if (strcmp(argv[i], "--repeats") == 0) {
            repeats = (size_t)strtoul(argv[i + 1], NULL, 10);
        }
    }
    if (lines == 0) {
        lines = 1;
    }

    /* Short strings from a small alphabet so duplicates are common. */
    char **data = calloc(lines, sizeof *data);
    if (data == NULL) {
        return 1;
    }
    unsigned state = 2463534242u;
    for (size_t i = 0; i < lines; i++) {
        data[i] = malloc(5);
        for (int c = 0; c < 4; c++) {
            data[i][c] = (char)('a' + (xs(&state) % 5));
        }
        data[i][4] = '\0';
    }

    size_t distinct_check = 0;
    unsigned long hash_check = 0;

    CF_BENCH_START;
    for (size_t r = 0; r < repeats; r++) {
        unsigned long h = 0;
        distinct_check += dl_run((const char *const *)data, lines, &h);
        hash_check ^= h;
    }
    CF_BENCH_STOP;

    printf("%zu %lu\n", distinct_check, hash_check);

    for (size_t i = 0; i < lines; i++) {
        free(data[i]);
    }
    free(data);
    return 0;
}
