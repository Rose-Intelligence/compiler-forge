/* Input: line 1 = N (byte count); line 2 = N space-separated byte values 0..255. */
#include "hexencode.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *read_line(void)
{
    static char buf[65536];
    if (!fgets(buf, sizeof buf, stdin)) return NULL;
    size_t n = strlen(buf);
    if (n > 0 && buf[n - 1] == '\n') buf[n - 1] = '\0';
    return buf;
}

int main(void)
{
    char *first = read_line();
    if (first == NULL) { printf("bytes=0\nhex=\n"); return 0; }
    size_t n = (size_t)strtoul(first, NULL, 10);
    unsigned char *data = calloc(n > 0 ? n : 1, 1);
    char *out = calloc(2 * n + 1, 1);
    if (data == NULL || out == NULL) return 1;
    char *p = read_line();
    char *cur = p ? p : (char *)"";
    for (size_t i = 0; i < n; i++) data[i] = (unsigned char)(strtoul(cur, &cur, 10) & 0xFF);

    hx_encoder *enc = hx_new();
    if (enc == NULL) return 1;
    hx_encode(enc, data, n, out);
    printf("bytes=%zu\n", n);
    printf("hex=%s\n", out);

    hx_free(enc);
    free(data); free(out);
    return 0;
}
