/* The delimiter scan, kept opaque in its own translation unit. Compiling
 * csvcut.c cannot see this body without LTO, so a per-field-access rescan stays
 * genuinely linear in the field index rather than being hoisted away. */
#include "internal.h"

size_t csv_next_delim(const char *s, size_t from, size_t len, char delim)
{
    for (size_t j = from; j < len; j++) {
        if (s[j] == delim) {
            return j;
        }
    }
    return len;
}
