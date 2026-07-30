/* Input: line 1 = "rows cols"; next rows lines each with cols integers;
 * remaining lines = queries "r0 c0 r1 c1". */
#include "boxsum.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *read_line(void)
{
    static char buf[8192];
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
        printf("cells=0\n");
        return 0;
    }
    unsigned long rows = 0, cols = 0;
    sscanf(first, "%lu %lu", &rows, &cols);
    size_t n = (size_t)rows * (size_t)cols;
    long *cells = calloc(n > 0 ? n : 1, sizeof *cells);
    if (cells == NULL) {
        return 1;
    }
    for (size_t r = 0; r < rows; r++) {
        char *line = read_line();
        char *p = line ? line : (char *)"";
        for (size_t c = 0; c < cols; c++) {
            cells[r * cols + c] = strtol(p, &p, 10);
        }
    }
    bs_grid *g = bs_build(cells, rows, cols);
    if (g == NULL) {
        return 1;
    }
    printf("cells=%zu\n", bs_rows(g) * bs_cols(g));

    char *q;
    size_t qi = 0;
    while ((q = read_line()) != NULL) {
        unsigned long r0 = 0, c0 = 0, r1 = 0, c1 = 0;
        if (sscanf(q, "%lu %lu %lu %lu", &r0, &c0, &r1, &c1) == 4) {
            printf("sum[%zu]=%ld\n", qi, bs_box_sum(g, r0, c0, r1, c1));
            printf("pos[%zu]=%zu\n", qi, bs_box_positive(g, r0, c0, r1, c1));
        }
        qi++;
    }
    bs_free(g);
    free(cells);
    return 0;
}
