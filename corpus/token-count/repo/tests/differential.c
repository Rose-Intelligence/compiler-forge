/* Differential harness for tokencount.
 *
 * Reads text from stdin and prints the resulting word counts as sorted JSON.
 * Sorting matters: the equivalence contract is about the counts, not about the
 * order a particular data structure happens to store them in, so a candidate
 * that switches to a hash table must not be failed for iterating differently.
 */
#include "tokencount.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int compare_entries(const void *lhs, const void *rhs)
{
    const tc_entry *a = (const tc_entry *)lhs;
    const tc_entry *b = (const tc_entry *)rhs;
    return strcmp(a->word, b->word);
}

int main(void)
{
    size_t capacity = 65536;
    size_t length = 0;
    char *input = malloc(capacity);
    if (input == NULL) {
        fprintf(stderr, "out of memory\n");
        return 1;
    }

    int c;
    while ((c = getchar()) != EOF) {
        if (length + 1 >= capacity) {
            capacity *= 2;
            char *grown = realloc(input, capacity);
            if (grown == NULL) {
                free(input);
                fprintf(stderr, "out of memory\n");
                return 1;
            }
            input = grown;
        }
        input[length++] = (char)c;
    }
    input[length] = '\0';

    tc_table table;
    tc_init(&table);
    if (tc_count_text(&table, input) != 0) {
        printf("{\"error\":\"count_failed\"}\n");
        free(input);
        return 1;
    }

    tc_entry *sorted = malloc(table.size * sizeof(tc_entry) + 1);
    if (sorted == NULL) {
        free(input);
        return 1;
    }
    memcpy(sorted, table.entries, table.size * sizeof(tc_entry));
    qsort(sorted, table.size, sizeof(tc_entry), compare_entries);

    printf("{\"distinct\":%zu,\"counts\":{", table.size);
    for (size_t i = 0; i < table.size; i++) {
        if (i > 0) {
            printf(",");
        }
        printf("\"%s\":%zu", sorted[i].word, sorted[i].count);
    }
    printf("},\"digest\":%lu}\n", tc_digest(&table));

    free(sorted);
    tc_free(&table);
    free(input);
    return 0;
}
