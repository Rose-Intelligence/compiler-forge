/* Packing key/value records into a flat wire buffer.
 *
 * Wire format, per record: key length (1 byte), key bytes, value length
 * (1 byte), value bytes. Records appear in the order given. That format is the
 * contract; how the packer arrives at it is not.
 */
#ifndef RECPACK_H
#define RECPACK_H

#include <stddef.h>

typedef struct {
    const char *key;
    const char *value;
} rp_record;

/* Bytes rp_pack would emit for these records. */
size_t rp_packed_size(const rp_record *records, size_t count);

/* Pack into a newly allocated buffer of *out_len bytes. Caller frees.
 * Returns NULL on allocation failure or if any field exceeds 255 bytes. */
unsigned char *rp_pack(const rp_record *records, size_t count, size_t *out_len);

/* Number of records in a packed buffer, or 0 if it is malformed. */
size_t rp_count(const unsigned char *packed, size_t len);

#endif /* RECPACK_H */
