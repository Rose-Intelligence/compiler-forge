/* Test suite for tokencount.
 *
 * These pin the behaviour, not the storage strategy. A candidate is free to
 * replace the flat array with a hash table; it is not free to change what any
 * of these observe.
 */
#include "tokencount.h"

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

static void test_empty(void)
{
    tc_table table;
    tc_init(&table);
    CHECK(table.size == 0, "new table is empty");
    CHECK(tc_get(&table, "absent") == 0, "missing word counts zero");
    CHECK(tc_digest(&table) == 0UL, "empty table digests to zero");
    tc_free(&table);
}

static void test_add_and_get(void)
{
    tc_table table;
    tc_init(&table);

    CHECK(tc_add(&table, "alpha") == 0, "first add succeeds");
    CHECK(tc_add(&table, "alpha") == 0, "repeat add succeeds");
    CHECK(tc_add(&table, "beta") == 0, "distinct add succeeds");

    CHECK(tc_get(&table, "alpha") == 2, "alpha counted twice");
    CHECK(tc_get(&table, "beta") == 1, "beta counted once");
    CHECK(tc_get(&table, "gamma") == 0, "gamma never added");
    CHECK(table.size == 2, "two distinct words, got %zu", table.size);

    tc_free(&table);
}

static void test_empty_word_ignored(void)
{
    tc_table table;
    tc_init(&table);
    CHECK(tc_add(&table, "") == 0, "empty word is accepted");
    CHECK(table.size == 0, "empty word is not stored");
    tc_free(&table);
}

static void test_count_text(void)
{
    tc_table table;
    tc_init(&table);

    CHECK(tc_count_text(&table, "  one two   two\tthree\nthree three  ") == 0,
          "tokenising succeeds");
    CHECK(tc_get(&table, "one") == 1, "one appears once");
    CHECK(tc_get(&table, "two") == 2, "two appears twice");
    CHECK(tc_get(&table, "three") == 3, "three appears three times");
    CHECK(table.size == 3, "three distinct words, got %zu", table.size);

    tc_free(&table);
}

static void test_growth(void)
{
    /* Forces several reallocations, which is where an off-by-one in a rewritten
     * growth strategy would show up. */
    tc_table table;
    tc_init(&table);

    char word[16];
    for (int i = 0; i < 200; i++) {
        snprintf(word, sizeof(word), "w%d", i);
        CHECK(tc_add(&table, word) == 0, "add %d succeeds", i);
    }
    CHECK(table.size == 200, "200 distinct words, got %zu", table.size);

    for (int i = 0; i < 200; i++) {
        snprintf(word, sizeof(word), "w%d", i);
        CHECK(tc_get(&table, word) == 1, "w%d still present", i);
    }
    tc_free(&table);
}

static void test_digest_is_order_independent(void)
{
    tc_table a;
    tc_table b;
    tc_init(&a);
    tc_init(&b);

    tc_count_text(&a, "x y y z z z");
    tc_count_text(&b, "z z z y y x");

    CHECK(tc_digest(&a) == tc_digest(&b),
          "digest does not depend on insertion order");

    tc_free(&a);
    tc_free(&b);
}

static void test_null_safety(void)
{
    CHECK(tc_get(NULL, "x") == 0, "NULL table reads as empty");
    CHECK(tc_digest(NULL) == 0UL, "NULL table digests to zero");
    CHECK(tc_add(NULL, "x") == 0, "NULL table absorbs adds");
    tc_free(NULL);
}

int main(void)
{
    test_empty();
    test_add_and_get();
    test_empty_word_ignored();
    test_count_text();
    test_growth();
    test_digest_is_order_independent();
    test_null_safety();

    if (failures == 0) {
        printf("all tests passed\n");
        return 0;
    }
    printf("%d test(s) failed\n", failures);
    return 1;
}
