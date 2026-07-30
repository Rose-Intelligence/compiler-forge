#include "csvcut.h"

#include <assert.h>
#include <stdio.h>

int main(void)
{
    csv_row *r = csv_parse("ab,cde,f,,ghij", ',');
    assert(r != NULL);
    assert(csv_fields(r) == 5);

    assert(csv_field_start(r, 0) == 0);
    assert(csv_field_len(r, 0) == 2);
    assert(csv_field_start(r, 1) == 3);
    assert(csv_field_len(r, 1) == 3);
    assert(csv_field_start(r, 2) == 7);
    assert(csv_field_len(r, 2) == 1);
    assert(csv_field_start(r, 3) == 9);   /* empty field */
    assert(csv_field_len(r, 3) == 0);
    assert(csv_field_start(r, 4) == 10);
    assert(csv_field_len(r, 4) == 4);

    assert(csv_field_start(r, 5) == CSV_NO_FIELD);
    assert(csv_field_len(r, 5) == 0);
    csv_free(r);

    csv_row *e = csv_parse("", ',');   /* empty line is one empty field */
    assert(csv_fields(e) == 1);
    assert(csv_field_start(e, 0) == 0);
    assert(csv_field_len(e, 0) == 0);
    csv_free(e);

    printf("ok\n");
    return 0;
}
