/* Differential harness: reads one case from stdin and prints every observable
 * the equivalence contract declares.
 *
 * Input format, one field per line:
 *   line 1      : value count N
 *   next N lines: the integer values
 *   remaining   : query lines "lo hi", each probed as sum and even-count
 */
#include "rangesum.h"

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
        printf("count=0\n");
        return 0;
    }
    size_t count = (size_t)strtoul(first, NULL, 10);

    long *values = calloc(count > 0 ? count : 1, sizeof *values);
    if (values == NULL) {
        return 1;
    }
    for (size_t i = 0; i < count; i++) {
        char *line = read_line();
        values[i] = line ? strtol(line, NULL, 10) : 0;
    }

    rs_table *t = rs_build(values, count);
    if (t == NULL) {
        return 1;
    }

    printf("count=%zu\n", rs_size(t));

    char *q;
    size_t qi = 0;
    while ((q = read_line()) != NULL) {
        unsigned long lo = 0;
        unsigned long hi = 0;
        if (sscanf(q, "%lu %lu", &lo, &hi) == 2) {
            printf("sum[%zu]=%ld\n", qi, rs_range_sum(t, (size_t)lo, (size_t)hi));
            printf("even[%zu]=%zu\n", qi, rs_range_even(t, (size_t)lo, (size_t)hi));
        }
        qi++;
    }

    rs_free(t);
    free(values);
    return 0;
}
