/* Internal helpers, in their own translation unit so the no-LTO toolchain cannot
 * inline them into the counting loop. */
#ifndef DEDUP_INTERNAL_H
#define DEDUP_INTERNAL_H

int dl_eq(const char *a, const char *b);
unsigned long dl_hash(const char *s);

#endif /* DEDUP_INTERNAL_H */
