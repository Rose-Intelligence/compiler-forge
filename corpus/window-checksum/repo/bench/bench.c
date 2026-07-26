#include "wchk.h"

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
    size_t bytes = 3000;
    size_t width = 24;

    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--bytes") == 0) {
            bytes = (size_t)strtoul(argv[i + 1], NULL, 10);
        } else if (strcmp(argv[i], "--width") == 0) {
            width = (size_t)strtoul(argv[i + 1], NULL, 10);
        }
    }
    if (bytes == 0) bytes = 1;
    if (width == 0) width = 1;
    if (width > bytes) width = bytes;

    unsigned char *data = malloc(bytes);
    uint32_t *out = malloc((bytes - width + 1) * sizeof *out);
    if (data == NULL || out == NULL) {
        return 1;
    }
    unsigned state = 2463534242u;
    for (size_t i = 0; i < bytes; i++) {
        data[i] = (unsigned char)(xs(&state) & 0xff);
    }

    size_t windows = 0;
    size_t divisible = 0;
    uint64_t checksum = 0;

    CF_BENCH_START;
    windows = wchk_scan(data, bytes, width, out);
    for (size_t i = 0; i < windows; i++) {
        checksum = checksum * 31 + out[i];
    }
    divisible = wchk_count_divisible(data, bytes, width, 7);
    CF_BENCH_STOP;

    printf("%zu %zu %llu\n", windows, divisible,
           (unsigned long long)(checksum % 1000000007ull));

    free(out);
    free(data);
    return 0;
}
