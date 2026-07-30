/* A delimited row, addressable by field.
 *
 * The handle is opaque: the header promises the field queries, never how the
 * row is stored. An optimization may keep an offset table or anything else as
 * long as every function below still answers identically.
 */
#ifndef CSVCUT_H
#define CSVCUT_H

#include <stddef.h>

#define CSV_NO_FIELD ((size_t)-1)

typedef struct csv_row csv_row;

/* Parse `line` split on `delim`. The line is copied. NULL on allocation
 * failure. An empty line is one empty field. */
csv_row *csv_parse(const char *line, char delim);

void csv_free(csv_row *row);

/* Number of fields. */
size_t csv_fields(const csv_row *row);

/* Byte offset where field `i` begins, or CSV_NO_FIELD if `i` is out of range. */
size_t csv_field_start(const csv_row *row, size_t i);

/* Length of field `i` (excluding the delimiter), or 0 if out of range. */
size_t csv_field_len(const csv_row *row, size_t i);

#endif /* CSVCUT_H */
