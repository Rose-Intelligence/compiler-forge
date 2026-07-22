/* Differential harness.
 *
 * Reads one case from stdin and prints a structural view of what the parser did
 * with it. The validator runs this against baseline and candidate with identical
 * hidden inputs and compares the output under the package's declared equivalence
 * discipline.
 *
 * Output is JSON because this package declares the `structural` discipline:
 * whitespace in the serialised form may move, the parse tree may not.
 */
#include "csvsplit.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void print_json_string(const char *s)
{
    putchar('"');
    for (const char *p = s; *p != '\0'; p++) {
        switch (*p) {
        case '"':  fputs("\\\"", stdout); break;
        case '\\': fputs("\\\\", stdout); break;
        case '\n': fputs("\\n", stdout); break;
        case '\r': fputs("\\r", stdout); break;
        case '\t': fputs("\\t", stdout); break;
        default:
            if ((unsigned char)*p < 0x20) {
                printf("\\u%04x", (unsigned char)*p);
            } else {
                putchar(*p);
            }
        }
    }
    putchar('"');
}

int main(int argc, char **argv)
{
    char delim = ',';
    if (argc > 2 && strcmp(argv[1], "--delim") == 0 && argv[2][0] != '\0') {
        delim = argv[2][0];
    }

    /* Read all of stdin. Cases are small; a single growing buffer is fine. */
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

    printf("{\"lines\":[");

    const char *line_start = input;
    int first_line = 1;

    while (*line_start != '\0') {
        const char *newline = strchr(line_start, '\n');
        const size_t line_len =
            (newline != NULL) ? (size_t)(newline - line_start) : strlen(line_start);

        char *line = malloc(line_len + 1);
        if (line == NULL) {
            break;
        }
        memcpy(line, line_start, line_len);
        line[line_len] = '\0';

        if (!first_line) {
            printf(",");
        }
        first_line = 0;

        csv_row row;
        if (csv_split(line, delim, &row) == 0) {
            printf("{\"count\":%zu,\"fields\":[", row.count);
            for (size_t i = 0; i < row.count; i++) {
                if (i > 0) {
                    printf(",");
                }
                print_json_string(csv_trim(row.fields[i]));
            }
            printf("],\"declared\":%zu}", csv_count_fields(line, delim));
            csv_row_free(&row);
        } else {
            printf("{\"error\":\"split_failed\"}");
        }

        free(line);
        if (newline == NULL) {
            break;
        }
        line_start = newline + 1;
    }

    printf("],\"checksum\":%lu}\n", csv_checksum(input, delim));

    free(input);
    return 0;
}
