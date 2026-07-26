#include "rle.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void round_trip(const char *s)
{
    size_t len = 0;
    unsigned char *enc = rle_encode(s, &len);
    assert(enc != NULL);
    assert(len == rle_encoded_size(s));

    char *back = rle_decode(enc, len);
    assert(back != NULL);
    assert(strcmp(back, s) == 0);

    free(back);
    free(enc);
}

int main(void)
{
    round_trip("");
    round_trip("a");
    round_trip("aaaa");
    round_trip("abcd");
    round_trip("aaabbbcccd");

    /* A run longer than 255 must split into more than one pair. */
    char long_run[600];
    memset(long_run, 'x', sizeof long_run - 1);
    long_run[sizeof long_run - 1] = '\0';
    round_trip(long_run);
    assert(rle_encoded_size(long_run) == 6);  /* 255 + 255 + 89 */

    /* Exact encoding of a known input. */
    size_t len = 0;
    unsigned char *enc = rle_encode("aab", &len);
    assert(enc != NULL);
    assert(len == 4);
    assert(enc[0] == 2 && enc[1] == 'a');
    assert(enc[2] == 1 && enc[3] == 'b');
    free(enc);

    assert(rle_encoded_size(NULL) == 0);
    assert(rle_encode(NULL, &len) == NULL);

    printf("ok\n");
    return 0;
}
