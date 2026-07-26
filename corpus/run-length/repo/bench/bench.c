/* Benchmark driver. The measured region is bracketed so input generation is not
 * counted, and the checksum is printed so the differential stage can compare
 * what the encoder actually produced. */
#include "rle.h"

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
    size_t bytes = 4000;
    size_t runs = 60;      /* mean run length as a percentage-ish knob */
    size_t repeats = 3;

    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--bytes") == 0) {
            bytes = (size_t)strtoul(argv[i + 1], NULL, 10);
        } else if (strcmp(argv[i], "--run") == 0) {
            runs = (size_t)strtoul(argv[i + 1], NULL, 10);
        } else if (strcmp(argv[i], "--repeats") == 0) {
            repeats = (size_t)strtoul(argv[i + 1], NULL, 10);
        }
    }
    if (bytes == 0) bytes = 1;
    if (runs == 0) runs = 1;

    char *text = malloc(bytes + 1);
    if (text == NULL) {
        return 1;
    }
    unsigned state = 2463534242u;
    size_t at = 0;
    while (at < bytes) {
        char c = (char)('a' + (xs(&state) % 4));
        size_t run = 1 + (xs(&state) % runs);
        for (size_t r = 0; r < run && at < bytes; r++) {
            text[at++] = c;
        }
    }
    text[bytes] = '\0';

    size_t checksum = 0;
    size_t declared = 0;

    CF_BENCH_START;
    for (size_t rep = 0; rep < repeats; rep++) {
        declared = rle_encoded_size(text);

        size_t len = 0;
        unsigned char *encoded = rle_encode(text, &len);
        if (encoded == NULL) {
            free(text);
            return 1;
        }
        for (size_t i = 0; i < len; i++) {
            checksum = checksum * 31 + encoded[i];
        }

        char *back = rle_decode(encoded, len);
        if (back == NULL || strcmp(back, text) != 0) {
            free(back);
            free(encoded);
            free(text);
            return 1;
        }
        free(back);
        free(encoded);
    }
    CF_BENCH_STOP;

    printf("%zu %zu %zu\n", bytes, declared, checksum % 1000000007u);
    free(text);
    return 0;
}
