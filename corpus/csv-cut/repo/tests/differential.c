/* Differential harness: reads one case from stdin and prints every observable.
 *
 * Input format, one field per line:
 *   line 1      : the CSV row (split on ',')
 *   remaining   : field indices to query, one per line
 */
#include "csvcut.h"

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
    char *line = read_line();
    if (line == NULL) {
        printf("fields=0\n");
        return 0;
    }
    csv_row *row = csv_parse(line, ',');
    if (row == NULL) {
        return 1;
    }
    printf("fields=%zu\n", csv_fields(row));

    char *q;
    size_t qi = 0;
    while ((q = read_line()) != NULL) {
        size_t i = (size_t)strtoul(q, NULL, 10);
        size_t start = csv_field_start(row, i);
        if (start == CSV_NO_FIELD) {
            printf("start[%zu]=none\n", qi);
        } else {
            printf("start[%zu]=%zu\n", qi, start);
        }
        printf("len[%zu]=%zu\n", qi, csv_field_len(row, i));
        qi++;
    }

    csv_free(row);
    return 0;
}
