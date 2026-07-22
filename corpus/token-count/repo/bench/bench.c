/* Benchmark driver for tokencount.
 *
 * The measured region is bracketed so that input generation is not counted; only
 * the tokenising and counting work is. The digest is printed so the differential
 * stage has something to compare.
 */
#include "tokencount.h"

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

static unsigned long next_random(unsigned long *state)
{
    *state = (*state * 6364136223846793005UL + 1442695040888963407UL);
    return (*state >> 33);
}

static char *generate_text(size_t tokens, size_t vocabulary, unsigned long seed)
{
    const size_t capacity = tokens * 12 + 1;
    char *buffer = malloc(capacity);
    if (buffer == NULL) {
        return NULL;
    }

    size_t offset = 0;
    unsigned long state = seed;

    for (size_t i = 0; i < tokens; i++) {
        const unsigned long word = next_random(&state) % vocabulary;
        offset += (size_t)snprintf(buffer + offset, capacity - offset, "w%lu ", word);
    }
    buffer[offset] = '\0';
    return buffer;
}

int main(int argc, char **argv)
{
    size_t tokens = 20000;
    size_t vocabulary = 600;
    size_t repeats = 2;
    unsigned long seed = 0xc0ffeeUL;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--tokens") == 0 && i + 1 < argc) {
            tokens = strtoul(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--vocabulary") == 0 && i + 1 < argc) {
            vocabulary = strtoul(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--repeats") == 0 && i + 1 < argc) {
            repeats = strtoul(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--seed") == 0 && i + 1 < argc) {
            seed = strtoul(argv[++i], NULL, 0);
        }
    }

    char *text = generate_text(tokens, vocabulary, seed);
    if (text == NULL) {
        fprintf(stderr, "out of memory\n");
        return 1;
    }

    unsigned long digest = 1UL;

    CF_BENCH_START;
    for (size_t r = 0; r < repeats; r++) {
        tc_table table;
        tc_init(&table);
        tc_count_text(&table, text);
        digest = digest * 31UL + tc_digest(&table);
        tc_free(&table);
    }
    CF_BENCH_STOP;

    printf("digest=%lu\n", digest);
    printf("tokens=%zu vocabulary=%zu repeats=%zu\n", tokens, vocabulary, repeats);

    free(text);
    return 0;
}
