#include "hexencode.h"

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
    size_t bytes = 64;
    size_t queries = 400;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--bytes") == 0) bytes = (size_t)strtoul(argv[i + 1], NULL, 10);
        else if (strcmp(argv[i], "--queries") == 0) queries = (size_t)strtoul(argv[i + 1], NULL, 10);
    }
    if (bytes == 0) bytes = 1;

    unsigned char *data = malloc(bytes);
    char *out = malloc(2 * bytes + 1);
    if (data == NULL || out == NULL) return 1;
    unsigned state = 2463534242u;
    for (size_t i = 0; i < bytes; i++) data[i] = (unsigned char)(xs(&state) & 0xFF);

    hx_encoder *enc = hx_new();
    if (enc == NULL) return 1;

    unsigned long check = 0;
    CF_BENCH_START;
    for (size_t q = 0; q < queries; q++) {
        size_t len = hx_encode(enc, data, bytes, out);
        for (size_t i = 0; i < len; i++) check += (unsigned char)out[i];
    }
    CF_BENCH_STOP;

    printf("%zu %lu\n", bytes, check);
    hx_free(enc);
    free(data); free(out);
    return 0;
}
