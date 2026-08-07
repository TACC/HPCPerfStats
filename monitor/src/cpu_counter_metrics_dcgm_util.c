/* Pure DCGM util math / blank sentinels (no libdcgm runtime). */
#include <limits.h>

#include "cpu_counter_metrics_dcgm_util.h"

/* Match third_party/nvidia-dcgm/dcgm_structs.h DCGM_FP64_BLANK / IS_BLANK. */
#define DCGM_CPU_FP64_BLANK 140737488355328.0
/* Match third_party/nvidia-dcgm/dcgm_structs.h DCGM_INT64_BLANK / IS_BLANK. */
#define DCGM_CPU_INT64_BLANK 0x7ffffffffffffff0LL

int dcgm_fp64_value_is_blank(double v)
{
  return (v >= DCGM_CPU_FP64_BLANK) ? 1 : 0;
}

int dcgm_int64_value_is_blank(long long v)
{
  return (v >= DCGM_CPU_INT64_BLANK) ? 1 : 0;
}

unsigned long long dcgm_watts_dbl_to_ull(double v)
{
  if (dcgm_fp64_value_is_blank(v) || v <= 0.0)
    return 0ULL;
  if (v >= (double)ULLONG_MAX)
    return ULLONG_MAX;
  return (unsigned long long)(v + 0.5);
}

int dcgm_host_cpu_hw_collect_active(int dcgm_ready, int papi_ready, int util_bufs_ok)
{
  return (dcgm_ready || papi_ready || util_bufs_ok) ? 1 : 0;
}

int dcgm_backend_retry_due(time_t now, time_t retry_after)
{
  if (retry_after <= 0)
    return 1;
  if (now <= 0)
    return 0;
  return (now >= retry_after) ? 1 : 0;
}

#ifdef MONITOR_CPU_BACKEND_DCGM

double dcgm_clamp_percent(double v)
{
  if (v < 0.0)
    return 0.0;
  if (v > 100.0)
    return 100.0;
  return v;
}

void dcgm_cpu_scale_util_if_fraction(struct dcgm_cpu_sample *s)
{
  if (s == NULL || s->util_total <= 0.0)
    return;
  if (s->util_total > 1.0001)
    return;
  s->util_total *= 100.0;
  s->util_user *= 100.0;
  s->util_nice *= 100.0;
  s->util_sys *= 100.0;
  s->util_irq *= 100.0;
}

static unsigned long long dcgm_jifs_total(const struct dcgm_cpu_jifs *j)
{
  return j->u + j->nice + j->sys + j->idle + j->iow + j->irq + j->sft + j->stl + j->gu + j->gn;
}

static unsigned long long dcgm_jifs_nid(const struct dcgm_cpu_jifs *j)
{
  return j->u + j->nice + j->sys + j->irq + j->sft + j->stl + j->gu + j->gn;
}

void dcgm_cpu_sample_from_jiffy_diff(struct dcgm_cpu_sample *s, const struct dcgm_cpu_jifs *cur,
                                     const struct dcgm_cpu_jifs *prev)
{
  unsigned long long pt, ct, pn, cn;
  unsigned long long d_tot, d_nid;
  unsigned long long d_u, d_ni, d_sy, d_iq, d_sft;

  if (s == NULL || cur == NULL || prev == NULL)
    return;
  pt = dcgm_jifs_total(prev);
  ct = dcgm_jifs_total(cur);
  pn = dcgm_jifs_nid(prev);
  cn = dcgm_jifs_nid(cur);
  if (ct < pt || cn < pn)
    return;
  d_tot = ct - pt;
  d_nid = cn - pn;
  if (d_tot == 0)
    return;
  s->util_total = dcgm_clamp_percent(100.0 * (double)d_nid / (double)d_tot);
  d_u = (cur->u >= prev->u) ? (cur->u - prev->u) : 0;
  d_ni = (cur->nice >= prev->nice) ? (cur->nice - prev->nice) : 0;
  d_sy = (cur->sys >= prev->sys) ? (cur->sys - prev->sys) : 0;
  d_iq = (cur->irq >= prev->irq) ? (cur->irq - prev->irq) : 0;
  d_sft = (cur->sft >= prev->sft) ? (cur->sft - prev->sft) : 0;
  s->util_user = dcgm_clamp_percent(100.0 * (double)(d_u + d_ni) / (double)d_tot);
  s->util_sys = dcgm_clamp_percent(100.0 * (double)d_sy / (double)d_tot);
  s->util_irq = dcgm_clamp_percent(100.0 * (double)(d_iq + d_sft) / (double)d_tot);
  s->util_nice = 0.0;
}

int dcgm_count_unique_sorted_ints(const int *sorted, int n)
{
  int i, nu;

  if (n <= 0 || sorted == NULL)
    return 0;
  nu = 1;
  for (i = 1; i < n; i++) {
    if (sorted[i] != sorted[i - 1])
      nu++;
  }
  return nu;
}

#endif /* MONITOR_CPU_BACKEND_DCGM */
