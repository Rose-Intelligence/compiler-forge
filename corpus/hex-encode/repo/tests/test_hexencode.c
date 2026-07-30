#include "hexencode.h"

#include <assert.h>
#include <string.h>
#include <stdio.h>

int main(void)
{
    hx_encoder *enc = hx_new();
    assert(enc != NULL);
    char out[32];

    unsigned char a[] = {0x00, 0x0f, 0xa5, 0xff};
    assert(hx_encode(enc, a, 4, out) == 8);
    assert(strcmp(out, "000fa5ff") == 0);

    unsigned char b[] = {0xde, 0xad, 0xbe, 0xef};
    hx_encode(enc, b, 4, out);
    assert(strcmp(out, "deadbeef") == 0);

    assert(hx_encode(enc, a, 0, out) == 0);
    assert(out[0] == '\0');

    hx_free(enc);
    printf("ok\n");
    return 0;
}
