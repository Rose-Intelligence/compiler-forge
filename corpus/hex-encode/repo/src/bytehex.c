/* Byte-to-hex in its own translation unit, so the encode loop calls it per byte
 * under the no-LTO toolchain rather than the compiler folding it into a table
 * on its own. A precomputed table is the optimization the reference makes. */
#include "internal.h"

static char nibble(unsigned v)
{
    return (char)(v < 10 ? '0' + v : 'a' + (v - 10));
}

void hx_byte(unsigned char b, char *out2)
{
    out2[0] = nibble((unsigned)b >> 4);
    out2[1] = nibble((unsigned)b & 0x0Fu);
}
