/* Benchmark driver.
 *
 * The measured region is bracketed by cf_bench_start / cf_bench_stop: Callgrind
 * runs with --instr-atstart=no, so building the key set is not counted and work
 * moved out of the region would measure as free.
 *
 * The checksum is printed. The differential stage compares it between baseline
 * and candidate, so a patch that changes what the index answers is caught before
 * anything is measured.
 */
#include "sortedindex.h"

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

static int cmp_str(const void *a, const void *b)
{
    return strcmp(*(const char *const *)a, *(const char *const *)b);
}

int main(int argc, char **argv)
{
    size_t keys = 2000;
    size_t queries = 400;

    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--keys") == 0) {
            keys = (size_t)strtoul(argv[i + 1], NULL, 10);
        } else if (strcmp(argv[i], "--queries") == 0) {
            queries = (size_t)strtoul(argv[i + 1], NULL, 10);
        }
    }
    if (keys == 0) {
        keys = 1;
    }

    char **raw = calloc(keys, sizeof *raw);
    if (raw == NULL) {
        return 1;
    }
    unsigned state = 2463534242u;
    for (size_t i = 0; i < keys; i++) {
        raw[i] = malloc(9);
        if (raw[i] == NULL) {
            return 1;
        }
        for (int c = 0; c < 8; c++) {
            raw[i][c] = (char)('a' + (xs(&state) % 26));
        }
        raw[i][8] = '\0';
    }
    qsort(raw, keys, sizeof *raw, cmp_str);

    si_index *index = si_build((const char *const *)raw, keys);
    if (index == NULL) {
        return 1;
    }

    size_t found = 0;
    size_t missing = 0;
    size_t prefixed = 0;

    CF_BENCH_START;
    for (size_t q = 0; q < queries; q++) {
        /* Half the lookups hit, half miss: a miss is the worst case for a scan
         * and the same cost as a hit for a search, which is the point. */
        const char *key = raw[(xs(&state) % keys)];
        if (si_lookup(index, key) != SI_NOT_FOUND) {
            found++;
        }
        char absent[9];
        for (int c = 0; c < 8; c++) {
            absent[c] = (char)('A' + (xs(&state) % 26));
        }
        absent[8] = '\0';
        if (si_lookup(index, absent) == SI_NOT_FOUND) {
            missing++;
        }
        char prefix[3];
        prefix[0] = (char)('a' + (xs(&state) % 26));
        prefix[1] = (char)('a' + (xs(&state) % 26));
        prefix[2] = '\0';
        prefixed += si_count_prefix(index, prefix);
    }
    CF_BENCH_STOP;

    printf("%zu %zu %zu %zu\n", si_size(index), found, missing, prefixed);

    si_free(index);
    for (size_t i = 0; i < keys; i++) {
        free(raw[i]);
    }
    free(raw);
    return 0;
}
