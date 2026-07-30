/* Differential harness. Input: line 1 = N, next N lines = the strings. */
#include "dedup.h"

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
        printf("distinct=0\nhashsum=0\n");
        return 0;
    }
    size_t count = (size_t)strtoul(first, NULL, 10);
    char **lines = calloc(count > 0 ? count : 1, sizeof *lines);
    if (lines == NULL) {
        return 1;
    }
    for (size_t i = 0; i < count; i++) {
        char *line = read_line();
        lines[i] = strdup(line ? line : "");
    }

    unsigned long h = 0;
    size_t distinct = dl_run((const char *const *)lines, count, &h);
    printf("distinct=%zu\n", distinct);
    printf("hashsum=%lu\n", h);

    for (size_t i = 0; i < count; i++) {
        free(lines[i]);
    }
    free(lines);
    return 0;
}
