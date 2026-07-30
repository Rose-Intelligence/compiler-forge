/* Input: line 1 = n; line 2 = the sparse vector (n ints); remaining lines =
 * dense query vectors (n ints each). */
#include "sparsedot.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *read_line(void)
{
    static char buf[16384];
    if (!fgets(buf, sizeof buf, stdin)) return NULL;
    size_t n = strlen(buf);
    if (n > 0 && buf[n - 1] == '\n') buf[n - 1] = '\0';
    return buf;
}

static long *parse_vec(char *line, size_t n)
{
    long *out = calloc(n > 0 ? n : 1, sizeof *out);
    if (out == NULL) return NULL;
    char *p = line ? line : (char *)"";
    for (size_t i = 0; i < n; i++) out[i] = strtol(p, &p, 10);
    return out;
}

int main(void)
{
    char *first = read_line();
    if (first == NULL) { printf("size=0\n"); return 0; }
    size_t n = (size_t)strtoul(first, NULL, 10);
    long *vecvals = parse_vec(read_line(), n);
    sv_vec *v = sv_build(vecvals, n);
    if (v == NULL) return 1;
    printf("size=%zu\n", sv_size(v));

    char *q;
    size_t qi = 0;
    while ((q = read_line()) != NULL) {
        long *qv = parse_vec(q, n);
        printf("dot[%zu]=%ld\n", qi, sv_dot(v, qv, n));
        printf("overlap[%zu]=%zu\n", qi, sv_overlap(v, qv, n));
        free(qv);
        qi++;
    }
    sv_free(v);
    free(vecvals);
    return 0;
}
