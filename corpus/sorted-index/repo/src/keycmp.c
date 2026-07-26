/* Key comparison, deliberately in its own translation unit.
 *
 * The pinned toolchain builds without LTO, so the compiler cannot see this
 * definition while compiling sortedindex.c. That is what keeps the linear scan
 * genuinely linear: an optimizer that could inline this would still not turn a
 * scan into a search, but it could hoist and vectorise enough to blur the
 * measurement. Keeping it opaque makes the algorithmic cost the thing being
 * measured.
 */
#include "sortedindex.h"

#include <string.h>

int si_key_cmp(const char *a, const char *b)
{
    return strcmp(a, b);
}

int si_has_prefix(const char *key, const char *prefix)
{
    return strncmp(key, prefix, strlen(prefix)) == 0;
}
