/* The project's own test suite.
 *
 * Validators mount this read-only and verify the inventory by hash, so a
 * candidate cannot pass by weakening what is checked here. These tests pin the
 * behaviour an optimization must preserve — not its implementation.
 */
#include "csvsplit.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;

#define CHECK(cond, ...)                                        \
    do {                                                        \
        if (!(cond)) {                                          \
            printf("FAIL %s:%d: ", __FILE__, __LINE__);         \
            printf(__VA_ARGS__);                                \
            printf("\n");                                       \
            failures++;                                         \
        }                                                       \
    } while (0)

static void test_count_fields(void)
{
    CHECK(csv_count_fields("", ',') == 0, "empty line has no fields");
    CHECK(csv_count_fields("a", ',') == 1, "single field");
    CHECK(csv_count_fields("a,b,c", ',') == 3, "three fields");
    CHECK(csv_count_fields("a,,c", ',') == 3, "empty middle field still counts");
    CHECK(csv_count_fields(",", ',') == 2, "lone delimiter yields two empty fields");
    CHECK(csv_count_fields("a|b", '|') == 2, "alternate delimiter");
}

static void test_split_basic(void)
{
    csv_row row;
    CHECK(csv_split("alpha,beta,gamma", ',', &row) == 0, "split succeeds");
    CHECK(row.count == 3, "three fields, got %zu", row.count);
    CHECK(strcmp(row.fields[0], "alpha") == 0, "first field");
    CHECK(strcmp(row.fields[1], "beta") == 0, "second field");
    CHECK(strcmp(row.fields[2], "gamma") == 0, "third field");
    csv_row_free(&row);
}

static void test_split_edges(void)
{
    csv_row row;

    CHECK(csv_split("", ',', &row) == 0, "empty input succeeds");
    CHECK(row.count == 0, "empty input yields no fields");
    csv_row_free(&row);

    CHECK(csv_split("a,,c", ',', &row) == 0, "empty middle field");
    CHECK(row.count == 3, "empty middle field preserved");
    CHECK(strcmp(row.fields[1], "") == 0, "middle field is empty");
    csv_row_free(&row);

    CHECK(csv_split(",lead", ',', &row) == 0, "leading delimiter");
    CHECK(row.count == 2, "leading delimiter yields two fields");
    CHECK(strcmp(row.fields[0], "") == 0, "first field empty");
    csv_row_free(&row);

    CHECK(csv_split("trail,", ',', &row) == 0, "trailing delimiter");
    CHECK(row.count == 2, "trailing delimiter yields two fields");
    CHECK(strcmp(row.fields[1], "") == 0, "last field empty");
    csv_row_free(&row);
}

static void test_trim(void)
{
    char a[] = "  padded  ";
    CHECK(strcmp(csv_trim(a), "padded") == 0, "trims both ends");

    char b[] = "none";
    CHECK(strcmp(csv_trim(b), "none") == 0, "leaves untrimmed input alone");

    char c[] = "   ";
    CHECK(strcmp(csv_trim(c), "") == 0, "all-whitespace becomes empty");

    char d[] = "";
    CHECK(strcmp(csv_trim(d), "") == 0, "empty stays empty");

    char e[] = "\t\ntabbed\r\n";
    CHECK(strcmp(csv_trim(e), "tabbed") == 0, "trims all ASCII whitespace");
}

static void test_checksum_stability(void)
{
    const char *text = "1,2,3\n4,5,6\n";
    const unsigned long first = csv_checksum(text, ',');
    const unsigned long second = csv_checksum(text, ',');
    CHECK(first == second, "checksum is deterministic");
    CHECK(first != 0UL, "checksum is not trivially zero");

    CHECK(csv_checksum("1,2,3\n4,5,6\n", ',') != csv_checksum("1,2,3\n4,5,7\n", ','),
          "checksum distinguishes different content");

    CHECK(csv_checksum(NULL, ',') == 0UL, "NULL input is handled");
}

static void test_checksum_ignores_padding(void)
{
    /* Fields are trimmed before hashing, so padding must not change the result.
     * An optimization that skips the trim will fail here. */
    CHECK(csv_checksum("  1 , 2 \n", ',') == csv_checksum("1,2\n", ','),
          "surrounding whitespace does not affect the checksum");
}

int main(void)
{
    test_count_fields();
    test_split_basic();
    test_split_edges();
    test_trim();
    test_checksum_stability();
    test_checksum_ignores_padding();

    if (failures == 0) {
        printf("all tests passed\n");
        return 0;
    }
    printf("%d test(s) failed\n", failures);
    return 1;
}
