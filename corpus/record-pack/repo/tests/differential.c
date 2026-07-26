/* Reads "key<TAB>value" lines on stdin and prints every declared observable. */
#include "recpack.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void)
{
    static char line[8192];
    rp_record recs[512];
    static char store[512][2][256];
    size_t n = 0;

    while (n < 512 && fgets(line, sizeof line, stdin)) {
        size_t len = strlen(line);
        if (len > 0 && line[len - 1] == '\n') {
            line[--len] = '\0';
        }
        char *tab = strchr(line, '\t');
        const char *key = line;
        const char *value = "";
        if (tab != NULL) {
            *tab = '\0';
            value = tab + 1;
        }
        snprintf(store[n][0], sizeof store[n][0], "%s", key);
        snprintf(store[n][1], sizeof store[n][1], "%s", value);
        recs[n].key = store[n][0];
        recs[n].value = store[n][1];
        n++;
    }

    printf("records=%zu\n", n);
    printf("declared=%zu\n", rp_packed_size(recs, n));

    size_t len = 0;
    unsigned char *packed = rp_pack(recs, n, &len);
    if (packed == NULL) {
        printf("packed=null\n");
        return 0;
    }
    printf("len=%zu\n", len);
    printf("count=%zu\n", rp_count(packed, len));
    for (size_t i = 0; i < len; i++) {
        printf("byte[%zu]=%u\n", i, (unsigned)packed[i]);
    }
    free(packed);
    return 0;
}
