#ifndef STATS_BUFFER_ROWS_H_
#define STATS_BUFFER_ROWS_H_

#include "stats_text_format.h"

struct stats_buffer;
void stats_buffer_append_enabled_type_rows(struct stats_buffer *sf,
                                           enum stats_row_tier tier);

/* Pure tier decision for a payload's sample rows. `phase` is an enum
 * collect_phase value (0 = fast-only, 1 = full). Slow tier off -> legacy rows;
 * schema (`$`) payloads and full-phase samples -> @full; otherwise @fast. */
enum stats_row_tier stats_buffer_row_tier_decide(int is_schema_payload,
                                                 int tier_enabled, int phase);

#endif
