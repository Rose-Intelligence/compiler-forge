/* Reads one line on stdin and prints every declared observable. */
#include "rle.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void)
{
    static char line[65536];
    if (!fgets(line, sizeof line, stdin)) {
        line[0] = '\0';
    }
    size_t n = strlen(line);
    if (n > 0 && line[n - 1] == '\n') {
        line[n - 1] = '\0';
    }

    printf("declared=%zu\n", rle_encoded_size(line));

    size_t len = 0;
    unsigned char *enc = rle_encode(line, &len);
    if (enc == NULL) {
        printf("encoded=null\n");
        return 0;
    }
    printf("len=%zu\n", len);
    for (size_t i = 0; i < len; i++) {
        printf("byte[%zu]=%u\n", i, (unsigned)enc[i]);
    }

    char *back = rle_decode(enc, len);
    printf("roundtrip=%s\n", (back && strcmp(back, line) == 0) ? "ok" : "broken");

    free(back);
    free(enc);
    return 0;
}
