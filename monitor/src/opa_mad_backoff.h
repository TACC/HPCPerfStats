#ifndef _OPA_MAD_BACKOFF_H_
#define _OPA_MAD_BACKOFF_H_

/* Bounded MAD failure backoff for host_opa (jitter control). */
int opa_mad_collect_cycle_ok(void);
void opa_mad_note_success(void);
void opa_mad_note_failure(void);

/* Unit-test helpers. */
void opa_mad_test_reset_backoff(void);
void opa_mad_test_set_fail_streak(unsigned long n);

#endif
