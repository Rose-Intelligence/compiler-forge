/* Benchmark driver.
 *
 * Two things matter here and both are load-bearing.
 *
 * First, the measured region is bracketed by cf_bench_start / cf_bench_stop.
 * Callgrind runs with --instr-atstart=no, so process startup, input generation
 * and teardown are not counted. Without that bracket a small kernel's real cost
 * is swamped by process setup, and an "optimization" that moves work outside the
 * region would measure as free.
 *
 * Second, the checksum is printed. The differential stage compares this output
 * between baseline and candidate, so a patch that changes what the parser
 * computes is caught before anything is measured.
 */
#include "csvsplit.h"

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

/* Deterministic input generation: the same seed must produce the same bytes on
 * every host, or two validators are not measuring the same program. */
static unsigned long next_random(unsigned long *state)
{
    *state = (*state * 6364136223846793005UL + 1442695040888963407UL);
    return (*state >> 33);
}

static char *generate_input(size_t lines, size_t fields_per_line, unsigned long seed)
{
    const size_t capacity = lines * fields_per_line * 16 + lines + 1;
    char *buffer = malloc(capacity);
    if (buffer == NULL) {
        return NULL;
    }

    size_t offset = 0;
    unsigned long state = seed;

    for (size_t line = 0; line < lines; line++) {
        for (size_t field = 0; field < fields_per_line; field++) {
            if (field > 0) {
                buffer[offset++] = ',';
            }
            /* A little leading whitespace so csv_trim has work to do. */
            if ((next_random(&state) & 3UL) == 0UL) {
                buffer[offset++] = ' ';
            }
            const unsigned long value = next_random(&state) % 100000UL;
            offset += (size_t)snprintf(buffer + offset, capacity - offset, "%lu", value);
        }
        buffer[offset++] = '\n';
    }
    buffer[offset] = '\0';
    return buffer;
}

int main(int argc, char **argv)
{
    size_t lines = 2000;
    size_t fields = 12;
    size_t repeats = 3;
    unsigned long seed = 0x5eedUL;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--lines") == 0 && i + 1 < argc) {
            lines = strtoul(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--fields") == 0 && i + 1 < argc) {
            fields = strtoul(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--repeats") == 0 && i + 1 < argc) {
            repeats = strtoul(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--seed") == 0 && i + 1 < argc) {
            seed = strtoul(argv[++i], NULL, 0);
        }
    }

    char *input = generate_input(lines, fields, seed);
    if (input == NULL) {
        fprintf(stderr, "out of memory\n");
        return 1;
    }

    /* Accumulated rather than XORed: an even number of identical repeats would
     * cancel to zero and the differential stage would compare nothing. */
    unsigned long checksum = 1UL;

    CF_BENCH_START;
    for (size_t r = 0; r < repeats; r++) {
        checksum = checksum * 31UL + csv_checksum(input, ',');
    }
    CF_BENCH_STOP;

    printf("checksum=%lu\n", checksum);
    printf("lines=%zu fields=%zu repeats=%zu\n", lines, fields, repeats);

    free(input);
    return 0;
}
