#include "recpack.h"

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
    size_t records = 900;
    size_t field = 12;
    size_t repeats = 3;

    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--records") == 0) {
            records = (size_t)strtoul(argv[i + 1], NULL, 10);
        } else if (strcmp(argv[i], "--field") == 0) {
            field = (size_t)strtoul(argv[i + 1], NULL, 10);
        } else if (strcmp(argv[i], "--repeats") == 0) {
            repeats = (size_t)strtoul(argv[i + 1], NULL, 10);
        }
    }
    if (records == 0) records = 1;
    if (field == 0) field = 1;
    if (field > 200) field = 200;

    rp_record *recs = calloc(records, sizeof *recs);
    char *pool = malloc(records * 2 * (field + 1));
    if (recs == NULL || pool == NULL) {
        return 1;
    }
    unsigned state = 2463534242u;
    size_t at = 0;
    for (size_t i = 0; i < records; i++) {
        for (int which = 0; which < 2; which++) {
            char *s = pool + at;
            size_t len = 1 + (xs(&state) % field);
            for (size_t c = 0; c < len; c++) {
                s[c] = (char)('a' + (xs(&state) % 26));
            }
            s[len] = '\0';
            at += len + 1;
            if (which == 0) {
                recs[i].key = s;
            } else {
                recs[i].value = s;
            }
        }
    }

    size_t declared = 0;
    size_t packed_len = 0;
    size_t checksum = 0;
    size_t counted = 0;

    CF_BENCH_START;
    for (size_t rep = 0; rep < repeats; rep++) {
        declared = rp_packed_size(recs, records);

        unsigned char *packed = rp_pack(recs, records, &packed_len);
        if (packed == NULL) {
            return 1;
        }
        for (size_t i = 0; i < packed_len; i++) {
            checksum = checksum * 31 + packed[i];
        }
        counted = rp_count(packed, packed_len);
        free(packed);
    }
    CF_BENCH_STOP;

    printf("%zu %zu %zu %zu\n", declared, packed_len, counted, checksum % 1000000007u);

    free(pool);
    free(recs);
    return 0;
}
