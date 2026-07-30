/* Correct, and rescanning from the start on every field access.
 *
 * The row never changes after parsing, yet reaching field i walks past i
 * delimiters from the beginning each time it is asked. Recording where each
 * field begins once — a single pass at parse time — makes every access O(1). The
 * information is there; nothing forms it.
 */
#include "csvcut.h"

#include <stdlib.h>
#include <string.h>

#include "internal.h"

struct csv_row {
    char *line;
    size_t len;
    char delim;
    size_t fields;
};

csv_row *csv_parse(const char *line, char delim)
{
    csv_row *row = calloc(1, sizeof *row);
    if (row == NULL) {
        return NULL;
    }
    row->len = strlen(line);
    row->line = malloc(row->len + 1);
    if (row->line == NULL) {
        free(row);
        return NULL;
    }
    memcpy(row->line, line, row->len + 1);
    row->delim = delim;

    size_t count = 1;
    for (size_t i = 0; i < row->len; i++) {
        if (row->line[i] == delim) {
            count++;
        }
    }
    row->fields = count;
    return row;
}

void csv_free(csv_row *row)
{
    if (row == NULL) {
        return;
    }
    free(row->line);
    free(row);
}

size_t csv_fields(const csv_row *row)
{
    return row == NULL ? 0 : row->fields;
}

size_t csv_field_start(const csv_row *row, size_t i)
{
    if (row == NULL || i >= row->fields) {
        return CSV_NO_FIELD;
    }
    /* Walk past i delimiters from the front, every call. */
    size_t pos = 0;
    for (size_t k = 0; k < i; k++) {
        pos = csv_next_delim(row->line, pos, row->len, row->delim) + 1;
    }
    return pos;
}

size_t csv_field_len(const csv_row *row, size_t i)
{
    if (row == NULL || i >= row->fields) {
        return 0;
    }
    size_t start = csv_field_start(row, i);
    size_t end = csv_next_delim(row->line, start, row->len, row->delim);
    return end - start;
}
