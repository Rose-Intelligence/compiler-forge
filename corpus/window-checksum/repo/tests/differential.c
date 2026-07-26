/* Reads a hex byte string on stdin and prints every declared observable. */
#include "wchk.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int nibble(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

int main(void)
{
    static char line[65536];
    if (!fgets(line, sizeof line, stdin)) {
        line[0] = '\0';
    }
    size_t n = strlen(line);
    while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r')) {
        line[--n] = '\0';
    }

    unsigned char *data = malloc(n / 2 + 1);
    if (data == NULL) {
        return 1;
    }
    size_t len = 0;
    for (size_t i = 0; i + 1 < n; i += 2) {
        int hi = nibble(line[i]);
        int lo = nibble(line[i + 1]);
        if (hi < 0 || lo < 0) {
            break;
        }
        data[len++] = (unsigned char)((hi << 4) | lo);
    }

    printf("len=%zu\n", len);

    for (size_t width = 1; width <= 5; width++) {
        uint32_t *out = malloc((len + 1) * sizeof *out);
        if (out == NULL) {
            return 1;
        }
        size_t windows = wchk_scan(data, len, width, out);
        printf("width=%zu windows=%zu divisible=%zu\n", width, windows,
               wchk_count_divisible(data, len, width, 7));
        for (size_t i = 0; i < windows; i++) {
            printf("w[%zu][%zu]=%u\n", width, i, out[i]);
        }
        free(out);
    }

    free(data);
    return 0;
}
