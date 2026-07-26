/* Field measurement, deliberately in its own translation unit.
 *
 * Without LTO the compiler cannot see this while compiling recpack.c, so it
 * cannot collapse the repeated measurement passes into one. The cost being
 * measured is the redundant work the code does, not the work the inliner
 * happens to leave behind.
 */
#include "internal.h"

#include <string.h>

size_t rp_field_len(const char *field)
{
    return field == NULL ? 0 : strlen(field);
}
