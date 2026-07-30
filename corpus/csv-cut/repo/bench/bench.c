/* Benchmark driver. The measured region — the field accesses — is bracketed by
 * CF_BENCH_START / CF_BENCH_STOP; parsing the row is outside it. */
#include "csvcut.h"

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
    size_t fields = 8;
    size_t width = 4;
    size_t queries = 400;

    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--fields") == 0) {
            fields = (size_t)strtoul(argv[i + 1], NULL, 10);
        } else if (strcmp(argv[i], "--width") == 0) {
            width = (size_t)strtoul(argv[i + 1], NULL, 10);
        } else if (strcmp(argv[i], "--queries") == 0) {
            queries = (size_t)strtoul(argv[i + 1], NULL, 10);
        }
    }
    if (fields == 0) {
        fields = 1;
    }

    /* Build a row of `fields` fields, each `width` letters, comma-separated. */
    size_t cap = fields * (width + 1) + 1;
    char *line = malloc(cap);
    if (line == NULL) {
        return 1;
    }
    unsigned state = 2463534242u;
    size_t at = 0;
    for (size_t f = 0; f < fields; f++) {
        if (f > 0) {
            line[at++] = ',';
        }
        for (size_t c = 0; c < width; c++) {
            line[at++] = (char)('a' + (xs(&state) % 26));
        }
    }
    line[at] = '\0';

    csv_row *row = csv_parse(line, ',');
    if (row == NULL) {
        return 1;
    }

    size_t start_check = 0;
    size_t len_check = 0;

    CF_BENCH_START;
    for (size_t q = 0; q < queries; q++) {
        size_t i = xs(&state) % fields;
        start_check += csv_field_start(row, i);
        len_check += csv_field_len(row, i);
    }
    CF_BENCH_STOP;

    printf("%zu %zu %zu\n", csv_fields(row), start_check, len_check);

    csv_free(row);
    free(line);
    return 0;
}
