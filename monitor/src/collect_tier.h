/* Two-tier collection state: slow-key table, enable flag, and the runtime
 * collect phase that gates which schema keys are collected/emitted per sample.
 *
 * When the slow tier is disabled, every key stays on the fast tier and behavior
 * is identical to the legacy single-tier monitor. */
#ifndef COLLECT_TIER_H
#define COLLECT_TIER_H

#include "schema.h"

struct stats_type;

/* Runtime collection phase. FAST_ONLY collects/emits only fast-tier keys;
 * FULL collects/emits every key (fast + slow). */
enum collect_phase {
  COLLECT_FAST_ONLY = 0,
  COLLECT_FULL = 1,
};

/* Master switch (config `enable_slow_tier`, default 1). When 0, the tier table
 * is not applied, no `,R=S` suffixes are emitted, rows are legacy full-width,
 * and collection gating is a no-op. */
void collect_tier_set_enabled(int enabled);
int collect_tier_enabled(void);

/* Current collect phase (process-global, single-threaded daemon). */
void collect_tier_set_phase(enum collect_phase phase);
enum collect_phase collect_tier_get_phase(void);

/* Phase to use for a given emission: `$`/schema (write_hdr) payloads are always
 * COLLECT_FULL so schema/rotation messages never become sparse. */
enum collect_phase collect_tier_effective_phase(int write_hdr);

/* Whether schema key `idx` of `type` is active (collected/emitted) in the
 * current phase. Always 1 when the slow tier is disabled or phase is FULL;
 * otherwise only fast-tier keys are active. */
int collect_tier_key_active(const struct stats_type *type, int idx);

/* Assign per-key tiers for `type` from the static slow-key table and the
 * `*_error`/`*_errors` auto-rule. No-op (leaves all keys fast) when the slow
 * tier is disabled. Idempotent. */
void collect_tier_apply_to_type(struct stats_type *type);

/* Test-only lookup: would (type_name,key) be slow under the current rules,
 * regardless of the enable flag? Returns 1 for slow, 0 for fast. */
int collect_tier_key_is_slow(const char *type_name, const char *key);

#endif
