/* Correct, and it measures everything three times.
 *
 * rp_packed_size walks every field to total the size. rp_pack then calls it,
 * throws the answer away by growing its buffer per record anyway, and measures
 * each field again as it writes. A fourth pass validates. Nothing here is
 * wrong; it is just paid for repeatedly.
 */
#include "recpack.h"

#include <stdlib.h>
#include <string.h>

#include "internal.h"

size_t rp_packed_size(const rp_record *records, size_t count)
{
    if (records == NULL) {
        return 0;
    }

    size_t total = 0;
    for (size_t i = 0; i < count; i++) {
        total += 1 + rp_field_len(records[i].key);
        total += 1 + rp_field_len(records[i].value);
    }
    return total;
}

static int fields_fit(const rp_record *records, size_t count)
{
    for (size_t i = 0; i < count; i++) {
        if (rp_field_len(records[i].key) > 255) {
            return 0;
        }
        if (rp_field_len(records[i].value) > 255) {
            return 0;
        }
    }
    return 1;
}

unsigned char *rp_pack(const rp_record *records, size_t count, size_t *out_len)
{
    if (out_len != NULL) {
        *out_len = 0;
    }
    if (records == NULL) {
        return NULL;
    }
    if (!fields_fit(records, count)) {
        return NULL;
    }

    unsigned char *out = NULL;
    size_t written = 0;

    for (size_t i = 0; i < count; i++) {
        /* Each field is measured again here, and the buffer is grown to fit
         * exactly this record, so every record copies everything before it. */
        size_t klen = rp_field_len(records[i].key);
        size_t vlen = rp_field_len(records[i].value);

        unsigned char *grown = realloc(out, written + 2 + klen + vlen);
        if (grown == NULL) {
            free(out);
            return NULL;
        }
        out = grown;

        out[written++] = (unsigned char)klen;
        memcpy(out + written, records[i].key ? records[i].key : "", klen);
        written += klen;
        out[written++] = (unsigned char)vlen;
        memcpy(out + written, records[i].value ? records[i].value : "", vlen);
        written += vlen;
    }

    if (out == NULL) {
        out = malloc(1);
        if (out == NULL) {
            return NULL;
        }
    }
    if (out_len != NULL) {
        *out_len = written;
    }
    return out;
}

size_t rp_count(const unsigned char *packed, size_t len)
{
    size_t at = 0;
    size_t seen = 0;
    while (at < len) {
        if (at + 1 > len) {
            return 0;
        }
        size_t klen = packed[at++];
        if (at + klen >= len) {
            return 0;
        }
        at += klen;
        size_t vlen = packed[at++];
        if (at + vlen > len) {
            return 0;
        }
        at += vlen;
        seen++;
    }
    return seen;
}
