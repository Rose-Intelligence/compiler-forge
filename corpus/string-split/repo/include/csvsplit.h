/* csvsplit — a small delimited-field parser.
 *
 * The public surface is deliberately tiny. Everything a validator compares for
 * API conformance is in this header, and an optimization that changes any of it
 * is rejected before it is ever measured.
 */
#ifndef CSVSPLIT_H
#define CSVSPLIT_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* One parsed row: `count` fields, each a NUL-terminated string. */
typedef struct {
    char **fields;
    size_t count;
} csv_row;

/* Split `line` on `delim`. Returns 0 on success, -1 on allocation failure.
 * The caller owns the result and must release it with csv_row_free. */
int csv_split(const char *line, char delim, csv_row *out);

/* Release a row produced by csv_split. Safe on a zeroed row. */
void csv_row_free(csv_row *row);

/* Count fields without materialising them. */
size_t csv_count_fields(const char *line, char delim);

/* Trim ASCII whitespace from both ends, in place. Returns `s`. */
char *csv_trim(char *s);

/* Checksum over every field of every line in `text`, used by the benchmark to
 * force the parse to actually happen. */
unsigned long csv_checksum(const char *text, char delim);

#ifdef __cplusplus
}
#endif

#endif /* CSVSPLIT_H */
