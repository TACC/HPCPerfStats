#include <inttypes.h>
#include <limits.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "collect.h"
#include "dcgm_agent.h"
#include "dcgm_structs.h"
#include "dcgm_fields.h"
#include "dcgm_session.h"
#include "nvidia_gpu.h"
#include "monitor_log.h"
#include "stats.h"
#include "trace.h"

/* Current DCGM uses DCGM_FI_DEV_CLOCK_THROTTLE_REASONS; older headers used *_CLOCKS_EVENT_*. */
#ifndef DCGM_FI_DEV_CLOCK_THROTTLE_REASONS
# ifdef DCGM_FI_DEV_CLOCKS_EVENT_REASONS
#  define DCGM_FI_DEV_CLOCK_THROTTLE_REASONS DCGM_FI_DEV_CLOCKS_EVENT_REASONS
# endif
#endif

#ifndef DCGM_FI_PROF_PCIE_TX_BYTES
#define DCGM_FI_PROF_PCIE_TX_BYTES 1009
#endif
#ifndef DCGM_FI_PROF_PCIE_RX_BYTES
#define DCGM_FI_PROF_PCIE_RX_BYTES 1010
#endif
#ifndef DCGM_FI_PROF_NVLINK_TX_BYTES
#define DCGM_FI_PROF_NVLINK_TX_BYTES 1011
#endif
#ifndef DCGM_FI_PROF_NVLINK_RX_BYTES
#define DCGM_FI_PROF_NVLINK_RX_BYTES 1012
#endif
#ifndef DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT
#define DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT 1132
#endif
#ifndef DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT
#define DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT 1133
#endif
/* Newer DCGM tensor splits (IMMA / HMMA); vendored dcgm_fields.h supplies these when missing. */
#ifndef DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE
#define DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE 1013
#endif
#ifndef DCGM_FI_PROF_PIPE_TENSOR_HMMA_ACTIVE
#define DCGM_FI_PROF_PIPE_TENSOR_HMMA_ACTIVE 1014
#endif

#define DBL_TO_LLU(x) ((unsigned long long) ((x) + 0.5))
#define DBL_TO_LLU_PERCENT(x) ((unsigned long long) ((100.0 * (x)) + 0.5))
#define I64_TO_LLU(x) ((unsigned long long) (x))

/*
 * Minimal DCGM watch list (works on stacks without PROF tensor IMMA/HMMA field IDs).
 * Must stay aligned with the full list minus the two optional tensor split fields.
 */
static const unsigned short g_dcgm_field_ids_core[NVIDIA_GPU_DCGM_NCORE] = {
  DCGM_FI_DEV_POWER_USAGE,
  DCGM_FI_DEV_GPU_TEMP,
  DCGM_FI_DEV_MEM_COPY_UTIL,
  DCGM_FI_DEV_GPU_UTIL,
  DCGM_FI_DEV_FB_TOTAL,
  DCGM_FI_DEV_FB_USED,
  DCGM_FI_PROF_PIPE_TENSOR_ACTIVE,
  DCGM_FI_PROF_PIPE_FP64_ACTIVE,
  DCGM_FI_PROF_PIPE_FP32_ACTIVE,
  DCGM_FI_PROF_PIPE_FP16_ACTIVE,
  DCGM_FI_PROF_SM_ACTIVE,
  DCGM_FI_PROF_SM_OCCUPANCY,
  DCGM_FI_DEV_CLOCK_THROTTLE_REASONS,
  DCGM_FI_PROF_PCIE_TX_BYTES,
  DCGM_FI_PROF_PCIE_RX_BYTES,
  DCGM_FI_PROF_NVLINK_TX_BYTES,
  DCGM_FI_PROF_NVLINK_RX_BYTES,
  DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT,
  DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT
};

/*
 * Non-PROF fallback for nodes where DCGM profiling watches are unsupported/permissioned off.
 * Keeps nvidia_gpu rows alive with basic telemetry and gpu_count.
 */
static const unsigned short g_dcgm_field_ids_basic[NVIDIA_GPU_DCGM_NBASIC] = {
  DCGM_FI_DEV_POWER_USAGE,
  DCGM_FI_DEV_GPU_TEMP,
  DCGM_FI_DEV_MEM_COPY_UTIL,
  DCGM_FI_DEV_GPU_UTIL,
  DCGM_FI_DEV_FB_TOTAL,
  DCGM_FI_DEV_FB_USED,
  DCGM_FI_DEV_CLOCK_THROTTLE_REASONS,
  DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT,
  DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT
};

static const unsigned short g_dcgm_field_ids[NVIDIA_GPU_NFIELDS] = {
  DCGM_FI_DEV_POWER_USAGE,
  DCGM_FI_DEV_GPU_TEMP,
  DCGM_FI_DEV_MEM_COPY_UTIL,
  DCGM_FI_DEV_GPU_UTIL,
  DCGM_FI_DEV_FB_TOTAL,
  DCGM_FI_DEV_FB_USED,
  DCGM_FI_PROF_PIPE_TENSOR_ACTIVE,
  DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE,
  DCGM_FI_PROF_PIPE_TENSOR_HMMA_ACTIVE,
  DCGM_FI_PROF_PIPE_FP64_ACTIVE,
  DCGM_FI_PROF_PIPE_FP32_ACTIVE,
  DCGM_FI_PROF_PIPE_FP16_ACTIVE,
  DCGM_FI_PROF_SM_ACTIVE,
  DCGM_FI_PROF_SM_OCCUPANCY,
  DCGM_FI_DEV_CLOCK_THROTTLE_REASONS,
  DCGM_FI_PROF_PCIE_TX_BYTES,
  DCGM_FI_PROF_PCIE_RX_BYTES,
  DCGM_FI_PROF_NVLINK_TX_BYTES,
  DCGM_FI_PROF_NVLINK_RX_BYTES,
  DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT,
  DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT
};

/* Coarse roofline approximations from utilization signals. */
#define NVIDIA_GPU_APPROX_PEAK_FLOPS_PER_S 60000000000000.0
#define NVIDIA_GPU_APPROX_PEAK_MEM_BW_BYTES_PER_S 1000000000000.0

static unsigned long long g_gpu_est_flops[DCGM_MAX_NUM_DEVICES];
static unsigned long long g_gpu_est_mem_read_bytes[DCGM_MAX_NUM_DEVICES];
static unsigned long long g_gpu_est_mem_write_bytes[DCGM_MAX_NUM_DEVICES];
static unsigned long long g_gpu_est_mem_total_bytes[DCGM_MAX_NUM_DEVICES];
static unsigned long long g_gpu_io_link_total_bytes[DCGM_MAX_NUM_DEVICES];
static uint64_t g_io_prev_pcie_tx[DCGM_MAX_NUM_DEVICES];
static uint64_t g_io_prev_pcie_rx[DCGM_MAX_NUM_DEVICES];
static uint64_t g_io_prev_nvlink_tx[DCGM_MAX_NUM_DEVICES];
static uint64_t g_io_prev_nvlink_rx[DCGM_MAX_NUM_DEVICES];
static unsigned char g_io_link_baseln[DCGM_MAX_NUM_DEVICES];
static long long g_gpu_prev_collect_us = 0;
static int g_last_watch_profile = -1;

enum nvidia_gpu_fail_stage {
  NVIDIA_GPU_FAIL_NONE = 0,
  NVIDIA_GPU_FAIL_DCGM_INIT,
  NVIDIA_GPU_FAIL_ATTACH,
  NVIDIA_GPU_FAIL_DISCOVERY,
  NVIDIA_GPU_FAIL_GROUP_CREATE,
  NVIDIA_GPU_FAIL_GROUP_ADD_DEVICE,
  NVIDIA_GPU_FAIL_FIELD_GROUP_CREATE,
  NVIDIA_GPU_FAIL_WATCH_FIELDS,
  NVIDIA_GPU_FAIL_ALLOC,
  NVIDIA_GPU_FAIL_FETCH,
  NVIDIA_GPU_FAIL_STAGE_NR
};

static unsigned long g_nvidia_gpu_fail_counts[NVIDIA_GPU_FAIL_STAGE_NR];
static unsigned long g_nvidia_gpu_gid_oob_skips;
static unsigned long g_nvidia_gpu_stats_alloc_skips;

static int env_int_or_default(const char *name, int fallback)
{
  const char *v = getenv(name);
  char *end = NULL;
  long parsed;
  if (v == NULL || *v == '\0')
    return fallback;
  parsed = strtol(v, &end, 10);
  if (end == v || *end != '\0')
    return fallback;
  if (parsed < 0)
    return fallback;
  if (parsed > 3600000L)
    return 3600000;
  return (int) parsed;
}

static int nvidia_gpu_watch_attempt_order(int order[3])
{
  int n = 0;
  int p;
  if (g_last_watch_profile >= 0 && g_last_watch_profile <= 2)
    order[n++] = g_last_watch_profile;
  for (p = 0; p < 3; p++) {
    int seen = 0;
    int i;
    for (i = 0; i < n; i++) {
      if (order[i] == p) {
        seen = 1;
        break;
      }
    }
    if (!seen)
      order[n++] = p;
  }
  return n;
}

static const char *dcgm_err(dcgmReturn_t rc)
{
  return errorString(rc);
}

static int nvidia_gpu_warmup_wait_latest_values(dcgmHandle_t dcgm_handle,
                                                dcgmGpuGrp_t group_id,
                                                dcgmFieldGrp_t field_group_id)
{
  int wait_ms = env_int_or_default("HPCPERFSTATS_DCGM_WARMUP_MS", 10000);
  int step_ms = 250;
  dcgmReturn_t rc = DCGM_ST_OK;
  int elapsed;
  if (wait_ms <= 0)
    return 0;
  for (elapsed = 0; elapsed < wait_ms; elapsed += step_ms) {
    rc = dcgmUpdateAllFields(dcgm_handle, 1);
    if (rc == DCGM_ST_OK)
      return 0;
    usleep((useconds_t) step_ms * 1000U);
  }
  ERROR("DCGM warmup wait exhausted (%dms): %s\n", wait_ms, dcgm_err(rc));
  return -1;
}

static unsigned long long clamp_double_to_ull(double v)
{
  if (v <= 0.0)
    return 0ULL;
  if (v >= (double) ULLONG_MAX)
    return ULLONG_MAX;
  return (unsigned long long) (v + 0.5);
}

static unsigned long long ull_add_sat(unsigned long long a, unsigned long long b)
{
  return (ULLONG_MAX - a < b) ? ULLONG_MAX : (a + b);
}

static int bounded_ratio(double v, double *out)
{
  if (v >= 0.0 && v <= 1.0) {
    *out = v;
    return 0;
  }
  return -1;
}

/*
 * DCGM reports PROF byte fields as int64 or fp64 depending on build; normalize to uint64 for deltas.
 */
static void dcgm_field_value_watts(const dcgmFieldValue_v1 *v, double *out)
{
  if (v->fieldType == DCGM_FT_DOUBLE) {
    *out = v->value.dbl;
    return;
  }
  if (v->fieldType == DCGM_FT_INT64)
    *out = (double) v->value.i64;
  else
    *out = 0.0;
}

static void dcgm_field_value_u64(const dcgmFieldValue_v1 *v, uint64_t *out)
{
  if (v->fieldType == DCGM_FT_DOUBLE) {
    *out = clamp_double_to_ull(v->value.dbl);
    return;
  }
  if (v->fieldType == DCGM_FT_INT64) {
    if (v->value.i64 <= 0)
      *out = 0;
    else
      *out = (uint64_t) v->value.i64;
    return;
  }
  *out = 0;
}

/*
 * DCGM PROF link byte metrics are documented as byte counts; GetLatestValues returns monotonic
 * hardware-style counters on typical stacks. If values decrease (counter reset), treat current as
 * delta since reset. First sample after monitor start establishes baseline (no backlog).
 */
static unsigned long long nvidia_gpu_link_u64_delta(uint64_t cur, uint64_t *prev)
{
  if (cur >= *prev) {
    unsigned long long d = (unsigned long long) (cur - *prev);

    *prev = cur;
    return d;
  }
  *prev = cur;
  return (unsigned long long) cur;
}

static void nvidia_gpu_io_link_accumulate(unsigned int gid, const dcgm_data_t *row)
{
  if (gid >= DCGM_MAX_NUM_DEVICES)
    return;

  if (!g_io_link_baseln[gid]) {
    g_io_prev_pcie_tx[gid] = row->prof_pcie_tx_bytes;
    g_io_prev_pcie_rx[gid] = row->prof_pcie_rx_bytes;
    g_io_prev_nvlink_tx[gid] = row->prof_nvlink_tx_bytes;
    g_io_prev_nvlink_rx[gid] = row->prof_nvlink_rx_bytes;
    g_io_link_baseln[gid] = 1;
    return;
  }

  g_gpu_io_link_total_bytes[gid] =
      ull_add_sat(g_gpu_io_link_total_bytes[gid],
                  nvidia_gpu_link_u64_delta(row->prof_pcie_tx_bytes, &g_io_prev_pcie_tx[gid]));
  g_gpu_io_link_total_bytes[gid] =
      ull_add_sat(g_gpu_io_link_total_bytes[gid],
                  nvidia_gpu_link_u64_delta(row->prof_pcie_rx_bytes, &g_io_prev_pcie_rx[gid]));
  g_gpu_io_link_total_bytes[gid] =
      ull_add_sat(g_gpu_io_link_total_bytes[gid],
                  nvidia_gpu_link_u64_delta(row->prof_nvlink_tx_bytes, &g_io_prev_nvlink_tx[gid]));
  g_gpu_io_link_total_bytes[gid] =
      ull_add_sat(g_gpu_io_link_total_bytes[gid],
                  nvidia_gpu_link_u64_delta(row->prof_nvlink_rx_bytes, &g_io_prev_nvlink_rx[gid]));
}

static int list_field_values(unsigned int gpu_id,
                             dcgmFieldValue_v1 *values,
                             int num_values,
                             void *userdata)
{
  int i;
  dcgm_data_t *data = (dcgm_data_t *) userdata;

  if (gpu_id >= DCGM_MAX_NUM_DEVICES)
    return -1;
  for (i = 0; i < num_values; i++) {
    if (values[i].status != DCGM_ST_OK)
      continue;
    switch (values[i].fieldId) {
      case DCGM_FI_DEV_GPU_TEMP:
        data[gpu_id].temperature = values[i].value.i64;
        break;
      case DCGM_FI_DEV_POWER_USAGE:
        dcgm_field_value_watts(&values[i], &data[gpu_id].power_usage);
        break;
      case DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT:
        dcgm_field_value_watts(&values[i], &data[gpu_id].sysio_power_usage);
        break;
      case DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT:
        dcgm_field_value_watts(&values[i], &data[gpu_id].module_power_usage);
        break;
      case DCGM_FI_DEV_GPU_UTIL:
        data[gpu_id].gpu_util = values[i].value.i64;
        break;
      case DCGM_FI_DEV_MEM_COPY_UTIL:
        data[gpu_id].mem_util = values[i].value.i64;
        break;
      case DCGM_FI_DEV_FB_TOTAL:
        data[gpu_id].fb_total_mb = values[i].value.i64;
        break;
      case DCGM_FI_DEV_FB_USED:
        data[gpu_id].fb_used_mb = values[i].value.i64;
        break;
      case DCGM_FI_PROF_SM_ACTIVE:
        (void) bounded_ratio(values[i].value.dbl, &data[gpu_id].sm_active);
        break;
      case DCGM_FI_PROF_SM_OCCUPANCY:
        (void) bounded_ratio(values[i].value.dbl, &data[gpu_id].sm_occupancy);
        break;
      case DCGM_FI_PROF_PIPE_FP64_ACTIVE:
        (void) bounded_ratio(values[i].value.dbl, &data[gpu_id].fp64_active);
        break;
      case DCGM_FI_PROF_PIPE_FP32_ACTIVE:
        (void) bounded_ratio(values[i].value.dbl, &data[gpu_id].fp32_active);
        break;
      case DCGM_FI_PROF_PIPE_FP16_ACTIVE:
        (void) bounded_ratio(values[i].value.dbl, &data[gpu_id].fp16_active);
        break;
      case DCGM_FI_PROF_PIPE_TENSOR_ACTIVE:
        (void) bounded_ratio(values[i].value.dbl, &data[gpu_id].tensor_active);
        break;
      case DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE:
        (void) bounded_ratio(values[i].value.dbl, &data[gpu_id].tensor_imma_active);
        break;
      case DCGM_FI_PROF_PIPE_TENSOR_HMMA_ACTIVE:
        (void) bounded_ratio(values[i].value.dbl, &data[gpu_id].tensor_hmma_active);
        break;
      case DCGM_FI_DEV_CLOCK_THROTTLE_REASONS:
        data[gpu_id].clocks_event_reasons = values[i].value.i64;
        break;
      case DCGM_FI_PROF_PCIE_TX_BYTES:
        dcgm_field_value_u64(&values[i], &data[gpu_id].prof_pcie_tx_bytes);
        break;
      case DCGM_FI_PROF_PCIE_RX_BYTES:
        dcgm_field_value_u64(&values[i], &data[gpu_id].prof_pcie_rx_bytes);
        break;
      case DCGM_FI_PROF_NVLINK_TX_BYTES:
        dcgm_field_value_u64(&values[i], &data[gpu_id].prof_nvlink_tx_bytes);
        break;
      case DCGM_FI_PROF_NVLINK_RX_BYTES:
        dcgm_field_value_u64(&values[i], &data[gpu_id].prof_nvlink_rx_bytes);
        break;
      default:
        break;
    }
  }
  return 0;
}

/*
 * dcgmGetAllSupportedDevices / dcgmGetEntityGroupEntities use an IN/OUT count in practice.
 * Older host engines cap lists at 16 GPUs (see DCGM_MAX_NUM_DEVICES comment in dcgm_structs.h).
 * Some builds also reject DCGM_GEGE_FLAG_ONLY_SUPPORTED with DCGM_ST_BADPARAM.
 */
#define NVIDIA_DCGM_GPU_LIST_LEGACY_CAP 16

static dcgmReturn_t nvidia_gpu_discover_gpu_ids(dcgmHandle_t h,
                                                unsigned int *gpu_ids,
                                                int *pndev)
{
  static const int caps[] = { DCGM_MAX_NUM_DEVICES, NVIDIA_DCGM_GPU_LIST_LEGACY_CAP };
  size_t ci;
  dcgmReturn_t rc = DCGM_ST_BADPARAM;

  for (ci = 0; ci < sizeof(caps) / sizeof(caps[0]); ci++) {
    *pndev = caps[ci];
    rc = dcgmGetAllSupportedDevices(h, gpu_ids, pndev);
    if (rc == DCGM_ST_OK)
      return rc;
    if (rc != DCGM_ST_BADPARAM && rc != DCGM_ST_NOT_SUPPORTED)
      return rc;
  }
  for (ci = 0; ci < sizeof(caps) / sizeof(caps[0]); ci++) {
    *pndev = caps[ci];
    rc = dcgmGetEntityGroupEntities(h,
                                    DCGM_FE_GPU,
                                    (dcgm_field_eid_t *)gpu_ids,
                                    pndev,
                                    0);
    if (rc == DCGM_ST_OK)
      return rc;
    if (rc != DCGM_ST_BADPARAM && rc != DCGM_ST_NOT_SUPPORTED)
      return rc;
  }
  for (ci = 0; ci < sizeof(caps) / sizeof(caps[0]); ci++) {
    *pndev = caps[ci];
    rc = dcgmGetEntityGroupEntities(h,
                                    DCGM_FE_GPU,
                                    (dcgm_field_eid_t *)gpu_ids,
                                    pndev,
                                    DCGM_GEGE_FLAG_ONLY_SUPPORTED);
    if (rc == DCGM_ST_OK)
      return rc;
    if (rc != DCGM_ST_BADPARAM && rc != DCGM_ST_NOT_SUPPORTED)
      return rc;
  }
  for (ci = 0; ci < sizeof(caps) / sizeof(caps[0]); ci++) {
    *pndev = caps[ci];
    rc = dcgmGetAllDevices(h, gpu_ids, pndev);
    if (rc == DCGM_ST_OK)
      return rc;
    if (rc != DCGM_ST_BADPARAM && rc != DCGM_ST_NOT_SUPPORTED)
      return rc;
  }
  return rc;
}

static int nvidia_gpu_collect_dev(struct stats *stats,
                                  const dcgm_data_t *row,
                                  unsigned int gid,
                                  int gpu_count,
                                  long long delta_us)
{
  double fp_mix;
  double flops_rate;
  double mem_bw_rate;
  unsigned long long delta_flops = 0ULL;
  unsigned long long delta_mem_bytes = 0ULL;
  unsigned long long delta_mem_read_bytes = 0ULL;
  unsigned long long delta_mem_write_bytes = 0ULL;

  /*
   * gpu_flops_rate uses fp_mix from scalar/tensor pipes only. tensor_imma_active and
   * tensor_hmma_active are reported for multiprecision visibility but omitted here: they
   * overlap tensor_active (1004) / HMMA semantics on many stacks and would double-count.
   */
  fp_mix = row->fp64_active + row->fp32_active + row->fp16_active + row->tensor_active;
  if (fp_mix < 0.0)
    fp_mix = 0.0;
  if (fp_mix > 1.0)
    fp_mix = 1.0;
  flops_rate = fp_mix * NVIDIA_GPU_APPROX_PEAK_FLOPS_PER_S;
  mem_bw_rate = ((double) row->mem_util / 100.0) * NVIDIA_GPU_APPROX_PEAK_MEM_BW_BYTES_PER_S;
  if (mem_bw_rate < 0.0)
    mem_bw_rate = 0.0;

  nvidia_gpu_io_link_accumulate(gid, row);

  if (delta_us > 0) {
    double dt_sec = (double) delta_us / 1000000.0;
    delta_flops = clamp_double_to_ull(flops_rate * dt_sec);
    delta_mem_bytes = clamp_double_to_ull(mem_bw_rate * dt_sec);
    delta_mem_read_bytes = delta_mem_bytes / 2ULL;
    delta_mem_write_bytes = delta_mem_bytes - delta_mem_read_bytes;
    g_gpu_est_flops[gid] = ull_add_sat(g_gpu_est_flops[gid], delta_flops);
    g_gpu_est_mem_total_bytes[gid] = ull_add_sat(g_gpu_est_mem_total_bytes[gid], delta_mem_bytes);
    g_gpu_est_mem_read_bytes[gid] = ull_add_sat(g_gpu_est_mem_read_bytes[gid], delta_mem_read_bytes);
    g_gpu_est_mem_write_bytes[gid] = ull_add_sat(g_gpu_est_mem_write_bytes[gid], delta_mem_write_bytes);
  }

  stats_set(stats, "temperature", I64_TO_LLU(row->temperature));
  stats_set(stats, "gpu_util", I64_TO_LLU(row->gpu_util));
  stats_set(stats, "mem_util", I64_TO_LLU(row->mem_util));
  stats_set(stats, "mem_total_mb", I64_TO_LLU(row->fb_total_mb));
  stats_set(stats, "mem_used_mb", I64_TO_LLU(row->fb_used_mb));
  stats_set(stats, "power_usage", DBL_TO_LLU(row->power_usage));
  stats_set(stats, "sysio_power_usage", DBL_TO_LLU(row->sysio_power_usage));
  stats_set(stats, "module_power_usage", DBL_TO_LLU(row->module_power_usage));
  stats_set(stats, "fp64_active", DBL_TO_LLU_PERCENT(row->fp64_active));
  stats_set(stats, "fp32_active", DBL_TO_LLU_PERCENT(row->fp32_active));
  stats_set(stats, "fp16_active", DBL_TO_LLU_PERCENT(row->fp16_active));
  stats_set(stats, "sm_active", DBL_TO_LLU_PERCENT(row->sm_active));
  stats_set(stats, "sm_occupancy", DBL_TO_LLU_PERCENT(row->sm_occupancy));
  stats_set(stats, "tensor_active", DBL_TO_LLU_PERCENT(row->tensor_active));
  stats_set(stats, "tensor_imma_active", DBL_TO_LLU_PERCENT(row->tensor_imma_active));
  stats_set(stats, "tensor_hmma_active", DBL_TO_LLU_PERCENT(row->tensor_hmma_active));
  stats_set(stats, "clocks_event_reasons", I64_TO_LLU(row->clocks_event_reasons));
  stats_set(stats, "gpu_flops_rate", clamp_double_to_ull(flops_rate));
  stats_set(stats, "gpu_mem_bw_bytes_rate", clamp_double_to_ull(mem_bw_rate));
  stats_set(stats, "gpu_flops", g_gpu_est_flops[gid]);
  stats_set(stats, "gpu_mem_read_bytes", g_gpu_est_mem_read_bytes[gid]);
  stats_set(stats, "gpu_mem_write_bytes", g_gpu_est_mem_write_bytes[gid]);
  stats_set(stats, "gpu_mem_total_bytes", g_gpu_est_mem_total_bytes[gid]);
  stats_set(stats, "gpu_io_link_total_bytes", g_gpu_io_link_total_bytes[gid]);
  stats_set(stats, "gpu_count", (unsigned long long) (gpu_count < 0 ? 0 : gpu_count));
  return 0;
}

static void nvidia_gpu_collect(struct stats_type *type)
{
  int i;
  int nr = 0;
  int ndev = 0;
  int dcgm_remote = 0;
  long long delta_us = 0;
  dcgmReturn_t rc;
  dcgmHandle_t dcgm_handle = (dcgmHandle_t) NULL;
  dcgmGpuGrp_t group_id = (dcgmGpuGrp_t) NULL;
  dcgmFieldGrp_t field_group_id = (dcgmFieldGrp_t) NULL;
  unsigned int gpu_ids[DCGM_MAX_NUM_DEVICES];
  dcgm_data_t *dcgm_data = NULL;
  int watch_profile = 0;
  int fail_stage = NVIDIA_GPU_FAIL_NONE;
  char group_name[] = "gpu_all";

  rc = dcgmInit();
  {
    struct timespec mono;
    if (clock_gettime(CLOCK_MONOTONIC, &mono) == 0) {
      long long now_us =
          (long long) mono.tv_sec * 1000000LL + (long long) mono.tv_nsec / 1000LL;
      if (g_gpu_prev_collect_us > 0 && now_us > g_gpu_prev_collect_us)
        delta_us = now_us - g_gpu_prev_collect_us;
      g_gpu_prev_collect_us = now_us;
    }
  }

  if (rc != DCGM_ST_OK) {
    fail_stage = NVIDIA_GPU_FAIL_DCGM_INIT;
    ERROR("DCGM init failed: %s\n", dcgm_err(rc));
    goto out;
  }

  rc = monitor_dcgm_attach_for_process(&dcgm_handle, &dcgm_remote);
  if (rc != DCGM_ST_OK || dcgm_handle == (dcgmHandle_t)0) {
    fail_stage = NVIDIA_GPU_FAIL_ATTACH;
    ERROR("DCGM attach failed (embedded or 127.0.0.1 hostengine): %s%s\n",
          dcgm_err(rc),
          rc == DCGM_ST_CONNECTION_NOT_VALID ? " (start nv-hostengine on this node?)" : "");
    goto out;
  }

  rc = nvidia_gpu_discover_gpu_ids(dcgm_handle, gpu_ids, &ndev);
  if (rc != DCGM_ST_OK) {
    fail_stage = NVIDIA_GPU_FAIL_DISCOVERY;
    ERROR("DCGM list devices failed: %s\n", dcgm_err(rc));
    goto out;
  }
  if (ndev <= 0) {
    ERROR("DCGM reports no supported GPUs\n");
    goto out;
  }

  rc = dcgmGroupCreate(dcgm_handle, DCGM_GROUP_EMPTY, group_name, &group_id);
  if (rc != DCGM_ST_OK) {
    fail_stage = NVIDIA_GPU_FAIL_GROUP_CREATE;
    ERROR("DCGM group creation failed: %s\n", dcgm_err(rc));
    goto out;
  }
  for (i = 0; i < ndev; i++) {
    rc = dcgmGroupAddDevice(dcgm_handle, group_id, gpu_ids[i]);
    if (rc != DCGM_ST_OK) {
      fail_stage = NVIDIA_GPU_FAIL_GROUP_ADD_DEVICE;
      ERROR("DCGM group add device gpu_id=%u failed: %s\n", gpu_ids[i], dcgm_err(rc));
      goto out;
    }
  }

  /*
   * Field-watch fallback ladder:
   *  0) full PROF list (includes tensor IMMA/HMMA split)
   *  1) core PROF list (legacy)
   *  2) non-PROF basic list (permissioned/unsupported profiling environments)
   */
  {
    int attempt_idx;
    int attempts[3] = {0, 1, 2};
    int attempt_nr = nvidia_gpu_watch_attempt_order(attempts);
    for (attempt_idx = 0; attempt_idx < attempt_nr; attempt_idx++) {
      int attempt = attempts[attempt_idx];
      unsigned int nf = 0;
      const unsigned short *fid = NULL;
      const char *profile_name = NULL;

      if (attempt == 0) {
        nf = (unsigned int) NVIDIA_GPU_NFIELDS;
        fid = g_dcgm_field_ids;
        profile_name = "full-prof";
      } else if (attempt == 1) {
        nf = (unsigned int) NVIDIA_GPU_DCGM_NCORE;
        fid = g_dcgm_field_ids_core;
        profile_name = "core-prof";
      } else {
        nf = (unsigned int) NVIDIA_GPU_DCGM_NBASIC;
        fid = g_dcgm_field_ids_basic;
        profile_name = "basic-nonprof";
      }

      if (field_group_id != (dcgmFieldGrp_t) NULL) {
        (void) dcgmFieldGroupDestroy(dcgm_handle, field_group_id);
        field_group_id = (dcgmFieldGrp_t) NULL;
      }

      rc = dcgmFieldGroupCreate(dcgm_handle,
                              nf,
                              (unsigned short *) fid,
                              (char *) "hpcperfstats_fields",
                              &field_group_id);
      if (rc != DCGM_ST_OK) {
        fail_stage = NVIDIA_GPU_FAIL_FIELD_GROUP_CREATE;
        if (attempt == 2)
          ERROR("DCGM field group creation failed: %s\n", dcgm_err(rc));
        else
          TRACE("DCGM field group creation failed for %s (will retry fallback): %s\n",
                profile_name, dcgm_err(rc));
        continue;
      }

      rc = dcgmWatchFields(dcgm_handle, group_id, field_group_id, 10000000, 3600.0, 3600);
      if (rc != DCGM_ST_OK) {
        fail_stage = NVIDIA_GPU_FAIL_WATCH_FIELDS;
        if (attempt == 2)
          ERROR("DCGM watch fields failed: %s\n", dcgm_err(rc));
        else
          TRACE("DCGM watch fields failed for %s (will retry fallback): %s\n",
                profile_name, dcgm_err(rc));
        (void) dcgmFieldGroupDestroy(dcgm_handle, field_group_id);
        field_group_id = (dcgmFieldGrp_t) NULL;
        continue;
      }
      watch_profile = attempt;
      g_last_watch_profile = watch_profile;
      break;
    }
    if (rc != DCGM_ST_OK || field_group_id == (dcgmFieldGrp_t) NULL)
      goto out;
  }
  if (watch_profile > 0) {
    monitor_log_warn("nvidia_gpu: using DCGM fallback watch profile %s\n",
                     watch_profile == 1 ? "core-prof" : "basic-nonprof");
  }
  if (nvidia_gpu_warmup_wait_latest_values(dcgm_handle, group_id, field_group_id) < 0)
    goto out;

  /*
   * dcgmGetLatestValues passes each GPU's DCGM id (not 0..ndev-1) to list_field_values.
   * Size the scratch array by DCGM_MAX_NUM_DEVICES so callbacks never write past the end.
   */
  dcgm_data = (dcgm_data_t *) calloc((size_t) DCGM_MAX_NUM_DEVICES, sizeof(*dcgm_data));
  if (dcgm_data == NULL) {
    fail_stage = NVIDIA_GPU_FAIL_ALLOC;
    ERROR("Failed to allocate DCGM data buffer\n");
    goto out;
  }

  rc = dcgmGetLatestValues(dcgm_handle, group_id, field_group_id, &list_field_values, dcgm_data);
  if (rc != DCGM_ST_OK) {
    fail_stage = NVIDIA_GPU_FAIL_FETCH;
    ERROR("DCGM fetch latest values failed: %s\n", dcgm_err(rc));
    goto out;
  }

  for (i = 0; i < ndev; i++) {
    struct stats *stats;
    char dev[80];
    unsigned int gid = gpu_ids[i];

    if (gid >= DCGM_MAX_NUM_DEVICES) {
      g_nvidia_gpu_gid_oob_skips++;
      continue;
    }

    snprintf(dev, sizeof(dev), "%d", i);
    stats = get_current_stats(type, dev);
    if (stats == NULL) {
      g_nvidia_gpu_stats_alloc_skips++;
      continue;
    }
    if (nvidia_gpu_collect_dev(stats, &dcgm_data[gid], gid, ndev, delta_us) == 0)
      nr++;
  }

out:
  if (fail_stage > NVIDIA_GPU_FAIL_NONE && fail_stage < NVIDIA_GPU_FAIL_STAGE_NR)
    g_nvidia_gpu_fail_counts[fail_stage]++;
  if (dcgm_data != NULL)
    free(dcgm_data);
  if (field_group_id != (dcgmFieldGrp_t) NULL)
    (void) dcgmFieldGroupDestroy(dcgm_handle, field_group_id);
  if (group_id != (dcgmGpuGrp_t) NULL)
    (void) dcgmGroupDestroy(dcgm_handle, group_id);
  if (dcgm_handle != (dcgmHandle_t)0) {
    if (dcgm_remote)
      (void) dcgmDisconnect(dcgm_handle);
#if !defined(MONITOR_CPU_BACKEND_DCGM)
    else
      (void) dcgmStopEmbedded(dcgm_handle);
#endif
    /*
     * When MONITOR_CPU_BACKEND_DCGM is set, cpu_counter_metrics owns a process-wide embedded
     * DCGM session (see dcgm_backend_begin). dcgmStopEmbedded/dcgmShutdown here leave its
     * g_dcgm_handle stale while st_begin still skips re-init (g_dcgm_ready stays 1); the next
     * dcgmUpdateAllFields in cpu_counter_metrics_collect can spiral CPU and syslog stays quiet.
     */
  }
#if !defined(MONITOR_CPU_BACKEND_DCGM)
  (void) dcgmShutdown();
#endif
  /*
   * Do not permanently disable nvidia_gpu on one failed cycle. DCGM profiling can be
   * transiently unavailable (hostengine restart, permission windows, unsupported PROF
   * subset on first attach). Keep the type enabled so later cycles can recover.
   */
  if (nr == 0)
    monitor_log_warn("nvidia_gpu: no device rows emitted this cycle; stage=%d init=%lu attach=%lu discover=%lu group=%lu add=%lu fg=%lu watch=%lu alloc=%lu fetch=%lu gid_oob=%lu stats_null=%lu\n",
                     fail_stage,
                     g_nvidia_gpu_fail_counts[NVIDIA_GPU_FAIL_DCGM_INIT],
                     g_nvidia_gpu_fail_counts[NVIDIA_GPU_FAIL_ATTACH],
                     g_nvidia_gpu_fail_counts[NVIDIA_GPU_FAIL_DISCOVERY],
                     g_nvidia_gpu_fail_counts[NVIDIA_GPU_FAIL_GROUP_CREATE],
                     g_nvidia_gpu_fail_counts[NVIDIA_GPU_FAIL_GROUP_ADD_DEVICE],
                     g_nvidia_gpu_fail_counts[NVIDIA_GPU_FAIL_FIELD_GROUP_CREATE],
                     g_nvidia_gpu_fail_counts[NVIDIA_GPU_FAIL_WATCH_FIELDS],
                     g_nvidia_gpu_fail_counts[NVIDIA_GPU_FAIL_ALLOC],
                     g_nvidia_gpu_fail_counts[NVIDIA_GPU_FAIL_FETCH],
                     g_nvidia_gpu_gid_oob_skips,
                     g_nvidia_gpu_stats_alloc_skips);
}

//! Definition of stats entry for this type
struct stats_type nvidia_gpu_stats_type = {
  .st_name = "nvidia_gpu",
  .st_collect = &nvidia_gpu_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
