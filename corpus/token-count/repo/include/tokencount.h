/* tokencount — a word frequency counter. */
#ifndef TOKENCOUNT_H
#define TOKENCOUNT_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    char *word;
    size_t count;
} tc_entry;

typedef struct {
    tc_entry *entries;
    size_t size;
    size_t capacity;
} tc_table;

/* Initialise an empty table. */
void tc_init(tc_table *table);

/* Record one occurrence of `word`. Returns 0 on success, -1 on allocation
 * failure. The table owns its copy of the string. */
int tc_add(tc_table *table, const char *word);

/* Occurrences of `word`, or 0 if it was never recorded. */
size_t tc_get(const tc_table *table, const char *word);

/* Release everything the table owns. Safe on a zeroed table. */
void tc_free(tc_table *table);

/* Tokenise `text` on ASCII whitespace and record every token. */
int tc_count_text(tc_table *table, const char *text);

/* Order-independent digest of the whole table, so the benchmark output does not
 * depend on internal iteration order. */
unsigned long tc_digest(const tc_table *table);

#ifdef __cplusplus
}
#endif

#endif /* TOKENCOUNT_H */
