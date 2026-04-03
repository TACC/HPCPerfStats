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

#define DBL_TO_LLU(x) ((unsigned long long) ((x) + 0.5))
#define DBL_TO_LLU_PERCENT(x) ((unsigned long long) ((100.0 * (x)) + 0.5))
#define I64_TO_LLU(x) ((unsigned long long) (x))

static const unsigned short g_dcgm_field_ids[NVIDIA_GPU_NFIELDS] = {
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
  DCGM_FI_PROF_NVLINK_RX_BYTES
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

static const char *dcgm_err(dcgmReturn_t rc)
{
  return errorString(rc);
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
        data[gpu_id].power_usage = values[i].value.dbl;
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
  stats_set(stats, "fp64_active", DBL_TO_LLU_PERCENT(row->fp64_active));
  stats_set(stats, "fp32_active", DBL_TO_LLU_PERCENT(row->fp32_active));
  stats_set(stats, "fp16_active", DBL_TO_LLU_PERCENT(row->fp16_active));
  stats_set(stats, "sm_active", DBL_TO_LLU_PERCENT(row->sm_active));
  stats_set(stats, "sm_occupancy", DBL_TO_LLU_PERCENT(row->sm_occupancy));
  stats_set(stats, "tensor_active", DBL_TO_LLU_PERCENT(row->tensor_active));
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
    ERROR("DCGM init failed: %s\n", dcgm_err(rc));
    goto out;
  }

  rc = monitor_dcgm_attach_for_process(&dcgm_handle, &dcgm_remote);
  if (rc != DCGM_ST_OK || dcgm_handle == (dcgmHandle_t)0) {
    ERROR("DCGM attach failed (embedded or 127.0.0.1 hostengine): %s%s\n",
          dcgm_err(rc),
          rc == DCGM_ST_CONNECTION_NOT_VALID ? " (start nv-hostengine on this node?)" : "");
    goto out;
  }

  rc = nvidia_gpu_discover_gpu_ids(dcgm_handle, gpu_ids, &ndev);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM list devices failed: %s\n", dcgm_err(rc));
    goto out;
  }
  if (ndev <= 0) {
    ERROR("DCGM reports no supported GPUs\n");
    goto out;
  }

  rc = dcgmGroupCreate(dcgm_handle, DCGM_GROUP_EMPTY, group_name, &group_id);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM group creation failed: %s\n", dcgm_err(rc));
    goto out;
  }
  for (i = 0; i < ndev; i++) {
    rc = dcgmGroupAddDevice(dcgm_handle, group_id, gpu_ids[i]);
    if (rc != DCGM_ST_OK) {
      ERROR("DCGM group add device gpu_id=%u failed: %s\n", gpu_ids[i], dcgm_err(rc));
      goto out;
    }
  }

  rc = dcgmFieldGroupCreate(dcgm_handle,
                            NVIDIA_GPU_NFIELDS,
                            (unsigned short *) g_dcgm_field_ids,
                            (char *) "hpcperfstats_fields",
                            &field_group_id);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM field group creation failed: %s\n", dcgm_err(rc));
    goto out;
  }

  rc = dcgmWatchFields(dcgm_handle, group_id, field_group_id, 10000000, 3600.0, 3600);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM watch fields failed: %s\n", dcgm_err(rc));
    goto out;
  }
  usleep(10000000);

  /*
   * dcgmGetLatestValues passes each GPU's DCGM id (not 0..ndev-1) to list_field_values.
   * Size the scratch array by DCGM_MAX_NUM_DEVICES so callbacks never write past the end.
   */
  dcgm_data = (dcgm_data_t *) calloc((size_t) DCGM_MAX_NUM_DEVICES, sizeof(*dcgm_data));
  if (dcgm_data == NULL) {
    ERROR("Failed to allocate DCGM data buffer\n");
    goto out;
  }

  rc = dcgmGetLatestValues(dcgm_handle, group_id, field_group_id, &list_field_values, dcgm_data);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM fetch latest values failed: %s\n", dcgm_err(rc));
    goto out;
  }

  for (i = 0; i < ndev; i++) {
    struct stats *stats;
    char dev[80];
    unsigned int gid = gpu_ids[i];

    if (gid >= DCGM_MAX_NUM_DEVICES)
      continue;

    snprintf(dev, sizeof(dev), "%d", i);
    stats = get_current_stats(type, dev);
    if (stats == NULL)
      continue;
    if (nvidia_gpu_collect_dev(stats, &dcgm_data[gid], gid, ndev, delta_us) == 0)
      nr++;
  }

out:
  if (dcgm_data != NULL)
    free(dcgm_data);
  if (field_group_id != (dcgmFieldGrp_t) NULL)
    (void) dcgmFieldGroupDestroy(dcgm_handle, field_group_id);
  if (group_id != (dcgmGpuGrp_t) NULL)
    (void) dcgmGroupDestroy(dcgm_handle, group_id);
  if (dcgm_handle != (dcgmHandle_t)0) {
    if (dcgm_remote)
      (void) dcgmDisconnect(dcgm_handle);
    else
      (void) dcgmStopEmbedded(dcgm_handle);
  }
  (void) dcgmShutdown();
  if (nr == 0)
    type->st_enabled = 0;
}

//! Definition of stats entry for this type
struct stats_type nvidia_gpu_stats_type = {
  .st_name = "nvidia_gpu",
  .st_collect = &nvidia_gpu_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
