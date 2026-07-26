/* Differential harness: reads one case from stdin and prints every observable
 * the equivalence contract declares.
 *
 * Input format, one field per line:
 *   line 1      : key count
 *   next N lines: keys, sorted ascending
 *   remaining   : query strings, each probed as both a lookup and a prefix
 */
#include "sortedindex.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *read_line(void)
{
    static char buf[4096];
    if (!fgets(buf, sizeof buf, stdin)) {
        return NULL;
    }
    size_t n = strlen(buf);
    if (n > 0 && buf[n - 1] == '\n') {
        buf[n - 1] = '\0';
    }
    return buf;
}

int main(void)
{
    char *first = read_line();
    if (first == NULL) {
        printf("keys=0\n");
        return 0;
    }
    size_t count = (size_t)strtoul(first, NULL, 10);

    char **keys = calloc(count > 0 ? count : 1, sizeof *keys);
    if (keys == NULL) {
        return 1;
    }
    for (size_t i = 0; i < count; i++) {
        char *line = read_line();
        keys[i] = strdup(line ? line : "");
        if (keys[i] == NULL) {
            return 1;
        }
    }

    si_index *ix = si_build((const char *const *)keys, count);
    if (ix == NULL) {
        return 1;
    }

    printf("keys=%zu\n", si_size(ix));

    char *q;
    size_t qi = 0;
    while ((q = read_line()) != NULL) {
        size_t at = si_lookup(ix, q);
        if (at == SI_NOT_FOUND) {
            printf("lookup[%zu]=absent\n", qi);
        } else {
            printf("lookup[%zu]=%zu\n", qi, at);
        }
        printf("prefix[%zu]=%zu\n", qi, si_count_prefix(ix, q));
        qi++;
    }

    si_free(ix);
    for (size_t i = 0; i < count; i++) {
        free(keys[i]);
    }
    free(keys);
    return 0;
}
