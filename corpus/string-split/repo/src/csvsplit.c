/* csvsplit — reference implementation.
 *
 * Correct, readable, and considerably more expensive than the job requires.
 * That combination is exactly the target this network exists to find: nothing
 * here is a bug, and every test passes.
 */
#include "csvsplit.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

size_t csv_count_fields(const char *line, char delim)
{
    if (line == NULL || *line == '\0') {
        return 0;
    }

    size_t count = 1;
    /* Recomputes the length on every iteration of the loop below. */
    for (size_t i = 0; i < strlen(line); i++) {
        if (line[i] == delim) {
            count++;
        }
    }
    return count;
}

char *csv_trim(char *s)
{
    if (s == NULL) {
        return s;
    }

    size_t start = 0;
    while (s[start] != '\0' && isspace((unsigned char)s[start])) {
        start++;
    }

    if (start > 0) {
        memmove(s, s + start, strlen(s + start) + 1);
    }

    size_t len = strlen(s);
    while (len > 0 && isspace((unsigned char)s[len - 1])) {
        s[len - 1] = '\0';
        len--;
    }
    return s;
}

int csv_split(const char *line, char delim, csv_row *out)
{
    if (out == NULL) {
        return -1;
    }

    out->fields = NULL;
    out->count = 0;

    if (line == NULL || *line == '\0') {
        return 0;
    }

    const size_t total = csv_count_fields(line, delim);
    char **fields = NULL;
    size_t written = 0;

    const char *cursor = line;
    for (size_t i = 0; i < total; i++) {
        const char *end = strchr(cursor, delim);
        const size_t span = (end != NULL) ? (size_t)(end - cursor) : strlen(cursor);

        /* Grows the array one element at a time: quadratic in copies. */
        char **grown = realloc(fields, ++written * sizeof(char *));
        if (grown == NULL) {
            for (size_t j = 0; j + 1 < written; j++) {
                free(fields[j]);
            }
            free(fields);
            return -1;
        }
        fields = grown;

        char *field = malloc(span + 1);
        if (field == NULL) {
            for (size_t j = 0; j + 1 < written; j++) {
                free(fields[j]);
            }
            free(fields);
            return -1;
        }
        memcpy(field, cursor, span);
        field[span] = '\0';
        fields[written - 1] = field;

        if (end == NULL) {
            break;
        }
        cursor = end + 1;
    }

    out->fields = fields;
    out->count = written;
    return 0;
}

void csv_row_free(csv_row *row)
{
    if (row == NULL || row->fields == NULL) {
        return;
    }
    for (size_t i = 0; i < row->count; i++) {
        free(row->fields[i]);
    }
    free(row->fields);
    row->fields = NULL;
    row->count = 0;
}

unsigned long csv_checksum(const char *text, char delim)
{
    if (text == NULL) {
        return 0UL;
    }

    unsigned long hash = 5381UL;
    const char *line_start = text;

    while (*line_start != '\0') {
        const char *newline = strchr(line_start, '\n');
        const size_t line_len =
            (newline != NULL) ? (size_t)(newline - line_start) : strlen(line_start);

        char *line = malloc(line_len + 1);
        if (line == NULL) {
            return hash;
        }
        memcpy(line, line_start, line_len);
        line[line_len] = '\0';

        csv_row row;
        if (csv_split(line, delim, &row) == 0) {
            for (size_t i = 0; i < row.count; i++) {
                char *field = csv_trim(row.fields[i]);
                for (size_t j = 0; field[j] != '\0'; j++) {
                    hash = ((hash << 5) + hash) + (unsigned char)field[j];
                }
                hash = ((hash << 5) + hash) + (unsigned long)i;
            }
            csv_row_free(&row);
        }
        free(line);

        if (newline == NULL) {
            break;
        }
        line_start = newline + 1;
    }

    return hash;
}
