/* tokencount — reference implementation.
 *
 * A flat array with a linear scan on every insert. Correct for any input, and
 * quadratic in the number of distinct words. The fix is a data-structure
 * change, which is the class of improvement no compiler flag can reach.
 */
#include "tokencount.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

void tc_init(tc_table *table)
{
    if (table == NULL) {
        return;
    }
    table->entries = NULL;
    table->size = 0;
    table->capacity = 0;
}

static tc_entry *tc_find(const tc_table *table, const char *word)
{
    /* Linear scan of every entry on every single insert and lookup. */
    for (size_t i = 0; i < table->size; i++) {
        if (strcmp(table->entries[i].word, word) == 0) {
            return &table->entries[i];
        }
    }
    return NULL;
}

int tc_add(tc_table *table, const char *word)
{
    if (table == NULL || word == NULL || *word == '\0') {
        return 0;
    }

    tc_entry *existing = tc_find(table, word);
    if (existing != NULL) {
        existing->count++;
        return 0;
    }

    if (table->size == table->capacity) {
        const size_t grown = (table->capacity == 0) ? 4 : table->capacity * 2;
        tc_entry *entries = realloc(table->entries, grown * sizeof(tc_entry));
        if (entries == NULL) {
            return -1;
        }
        table->entries = entries;
        table->capacity = grown;
    }

    char *copy = malloc(strlen(word) + 1);
    if (copy == NULL) {
        return -1;
    }
    memcpy(copy, word, strlen(word) + 1);

    table->entries[table->size].word = copy;
    table->entries[table->size].count = 1;
    table->size++;
    return 0;
}

size_t tc_get(const tc_table *table, const char *word)
{
    if (table == NULL || word == NULL) {
        return 0;
    }
    const tc_entry *entry = tc_find(table, word);
    return (entry != NULL) ? entry->count : 0;
}

void tc_free(tc_table *table)
{
    if (table == NULL || table->entries == NULL) {
        return;
    }
    for (size_t i = 0; i < table->size; i++) {
        free(table->entries[i].word);
    }
    free(table->entries);
    table->entries = NULL;
    table->size = 0;
    table->capacity = 0;
}

int tc_count_text(tc_table *table, const char *text)
{
    if (table == NULL || text == NULL) {
        return 0;
    }

    const char *cursor = text;
    while (*cursor != '\0') {
        while (*cursor != '\0' && isspace((unsigned char)*cursor)) {
            cursor++;
        }
        if (*cursor == '\0') {
            break;
        }

        const char *start = cursor;
        while (*cursor != '\0' && !isspace((unsigned char)*cursor)) {
            cursor++;
        }

        const size_t length = (size_t)(cursor - start);
        char *word = malloc(length + 1);
        if (word == NULL) {
            return -1;
        }
        memcpy(word, start, length);
        word[length] = '\0';

        const int rc = tc_add(table, word);
        free(word);
        if (rc != 0) {
            return rc;
        }
    }
    return 0;
}

unsigned long tc_digest(const tc_table *table)
{
    if (table == NULL) {
        return 0UL;
    }

    /* Summed rather than chained, so the digest does not depend on the order
     * entries happen to be stored in. A candidate is free to change that order;
     * it is not free to change the counts. */
    unsigned long total = 0UL;
    for (size_t i = 0; i < table->size; i++) {
        unsigned long hash = 5381UL;
        for (const char *p = table->entries[i].word; *p != '\0'; p++) {
            hash = ((hash << 5) + hash) + (unsigned char)*p;
        }
        total += hash * (unsigned long)table->entries[i].count;
    }
    return total;
}
