/* String equality and hash, kept opaque so the O(n^2) pairwise scan stays real:
 * without LTO the counting loop cannot see these bodies and collapse them. */
#include "internal.h"

#include <string.h>

int dl_eq(const char *a, const char *b)
{
    return strcmp(a, b) == 0;
}

unsigned long dl_hash(const char *s)
{
    unsigned long h = 1469598103934665603UL;   /* FNV-1a */
    for (; *s; s++) {
        h ^= (unsigned char)*s;
        h *= 1099511628211UL;
    }
    return h;
}
