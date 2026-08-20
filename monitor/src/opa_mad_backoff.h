#ifndef _OPA_MAD_BACKOFF_H_
#define _OPA_MAD_BACKOFF_H_

/* Bounded MAD failure backoff for host_opa (jitter control).
 * Backoff must exceed typical sample cadence (~150s) so skip actually suppresses MAD. */
#define OPA_MAD_FAIL_STREAK_MAX 8
#define OPA_MAD_BACKOFF_SEC 300

int opa_mad_collect_cycle_ok(void);
void opa_mad_note_success(void);
void opa_mad_note_failure(void);

/* Unit-test helpers. */
void opa_mad_test_reset_backoff(void);
void opa_mad_test_set_fail_streak(unsigned long n);

#endif
