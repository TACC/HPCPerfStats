/* NVIDIA GPU DCGM watch/setup (groups, field profiles, warmup). */
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
#include "dcgm_gpu_api.h"
#include "dcgm_session.h"
#include "nvidia_gpu.h"
#include "monitor_log.h"
#include "stats.h"
#include "nvidia_gpu_dcgm_compat.h"
#include "nvidia_gpu_dcgm_watch.h"

#define dcgm_err nvidia_gpu_dcgm_err

/*
 * Core DCGM watch list (works on stacks without PROF tensor IMMA/HMMA/DFMA field IDs).
 * Must stay aligned with the full list minus the three optional tensor split fields.
 */
static const unsigned short g_dcgm_field_ids_core[NVIDIA_GPU_DCGM_NCORE] = {
    DCGM_FI_DEV_POWER_USAGE,
    DCGM_FI_DEV_GPU_TEMP,
    DCGM_FI_DEV_MEM_COPY_UTIL,
    DCGM_FI_DEV_GPU_UTIL,
    DCGM_FI_DEV_FB_TOTAL,
    DCGM_FI_DEV_FB_USED,
    DCGM_FI_DEV_FB_FREE,
    DCGM_FI_DEV_SM_CLOCK,
    DCGM_FI_DEV_PCIE_REPLAY_COUNTER,
    DCGM_FI_PROF_PIPE_TENSOR_ACTIVE,
    DCGM_FI_PROF_PIPE_FP64_ACTIVE,
    DCGM_FI_PROF_PIPE_FP32_ACTIVE,
    DCGM_FI_PROF_PIPE_FP16_ACTIVE,
    DCGM_FI_PROF_SM_ACTIVE,
    DCGM_FI_PROF_SM_OCCUPANCY,
    DCGM_FI_PROF_DRAM_ACTIVE,
    DCGM_FI_DEV_CLOCK_THROTTLE_REASONS,
    DCGM_FI_PROF_PCIE_TX_BYTES,
    DCGM_FI_PROF_PCIE_RX_BYTES,
    DCGM_FI_PROF_NVLINK_TX_BYTES,
    DCGM_FI_PROF_NVLINK_RX_BYTES,
    DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT,
    DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT};

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
    DCGM_FI_DEV_FB_FREE,
    DCGM_FI_DEV_SM_CLOCK,
    DCGM_FI_DEV_PCIE_REPLAY_COUNTER,
    DCGM_FI_DEV_CLOCK_THROTTLE_REASONS,
    DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT,
    DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT};

static const unsigned short g_dcgm_field_ids[NVIDIA_GPU_NFIELDS] = {
    DCGM_FI_DEV_POWER_USAGE,
    DCGM_FI_DEV_GPU_TEMP,
    DCGM_FI_DEV_MEM_COPY_UTIL,
    DCGM_FI_DEV_GPU_UTIL,
    DCGM_FI_DEV_FB_TOTAL,
    DCGM_FI_DEV_FB_USED,
    DCGM_FI_DEV_FB_FREE,
    DCGM_FI_DEV_SM_CLOCK,
    DCGM_FI_DEV_PCIE_REPLAY_COUNTER,
    DCGM_FI_PROF_PIPE_TENSOR_ACTIVE,
    DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE,
    DCGM_FI_PROF_PIPE_TENSOR_HMMA_ACTIVE,
    DCGM_FI_PROF_PIPE_TENSOR_DFMA_ACTIVE,
    DCGM_FI_PROF_PIPE_FP64_ACTIVE,
    DCGM_FI_PROF_PIPE_FP32_ACTIVE,
    DCGM_FI_PROF_PIPE_FP16_ACTIVE,
    DCGM_FI_PROF_SM_ACTIVE,
    DCGM_FI_PROF_SM_OCCUPANCY,
    DCGM_FI_PROF_DRAM_ACTIVE,
    DCGM_FI_DEV_CLOCK_THROTTLE_REASONS,
    DCGM_FI_PROF_PCIE_TX_BYTES,
    DCGM_FI_PROF_PCIE_RX_BYTES,
    DCGM_FI_PROF_NVLINK_TX_BYTES,
    DCGM_FI_PROF_NVLINK_RX_BYTES,
    DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT,
    DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT};

_Static_assert(sizeof(g_dcgm_field_ids) / sizeof(g_dcgm_field_ids[0]) == NVIDIA_GPU_NFIELDS,
               "g_dcgm_field_ids length must match NVIDIA_GPU_NFIELDS");

static int g_last_watch_profile = -1;

unsigned long g_nvidia_gpu_fail_counts[NVIDIA_GPU_FAIL_STAGE_NR];
unsigned long g_nvidia_gpu_gid_oob_skips;
unsigned long g_nvidia_gpu_stats_alloc_skips;
int g_nvidia_gpu_warmup_done;
static int g_nvidia_gpu_warmup_profile = -1;
static int g_nvidia_gpu_runtime_ready;
static int g_nvidia_gpu_runtime_remote;
int g_nvidia_gpu_runtime_ndev;
static int g_nvidia_gpu_runtime_watch_profile = -1;
dcgmHandle_t g_nvidia_gpu_runtime_handle = (dcgmHandle_t)0;
dcgmGpuGrp_t g_nvidia_gpu_runtime_group = (dcgmGpuGrp_t)NULL;
dcgmFieldGrp_t g_nvidia_gpu_runtime_field_group = (dcgmFieldGrp_t)NULL;
unsigned int g_nvidia_gpu_runtime_gpu_ids[DCGM_MAX_NUM_DEVICES];

#define NVIDIA_DCGM_GPU_LIST_LEGACY_CAP 16

static dcgmReturn_t nvidia_gpu_discover_gpu_ids(dcgmHandle_t h, unsigned int *gpu_ids, int *pndev)
{
  static const int caps[] = {DCGM_MAX_NUM_DEVICES, NVIDIA_DCGM_GPU_LIST_LEGACY_CAP};
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
    rc = dcgmGetEntityGroupEntities(h, DCGM_FE_GPU, (dcgm_field_eid_t *)gpu_ids, pndev, 0);
    if (rc == DCGM_ST_OK)
      return rc;
    if (rc != DCGM_ST_BADPARAM && rc != DCGM_ST_NOT_SUPPORTED)
      return rc;
  }
  for (ci = 0; ci < sizeof(caps) / sizeof(caps[0]); ci++) {
    *pndev = caps[ci];
    rc = dcgmGetEntityGroupEntities(h, DCGM_FE_GPU, (dcgm_field_eid_t *)gpu_ids, pndev,
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
  return (int)parsed;
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

static int nvidia_gpu_warmup_wait_latest_values(dcgmHandle_t dcgm_handle, dcgmGpuGrp_t group_id,
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
    usleep((useconds_t)step_ms * 1000U);
  }
  ERROR("DCGM warmup wait exhausted (%dms): %s\n", wait_ms, dcgm_err(rc));
  return -1;
}

static int nvidia_gpu_maybe_warmup(dcgmHandle_t dcgm_handle, dcgmGpuGrp_t group_id,
                                   dcgmFieldGrp_t field_group_id, int watch_profile)
{
  if (g_nvidia_gpu_warmup_done && g_nvidia_gpu_warmup_profile == watch_profile)
    return 0;
  if (nvidia_gpu_warmup_wait_latest_values(dcgm_handle, group_id, field_group_id) < 0)
    return -1;
  g_nvidia_gpu_warmup_done = 1;
  g_nvidia_gpu_warmup_profile = watch_profile;
  return 0;
}

void nvidia_gpu_runtime_cleanup(void)
{
  if (g_nvidia_gpu_runtime_field_group != (dcgmFieldGrp_t)NULL) {
    (void)dcgmFieldGroupDestroy(g_nvidia_gpu_runtime_handle, g_nvidia_gpu_runtime_field_group);
    g_nvidia_gpu_runtime_field_group = (dcgmFieldGrp_t)NULL;
  }
  if (g_nvidia_gpu_runtime_group != (dcgmGpuGrp_t)NULL) {
    (void)dcgmGroupDestroy(g_nvidia_gpu_runtime_handle, g_nvidia_gpu_runtime_group);
    g_nvidia_gpu_runtime_group = (dcgmGpuGrp_t)NULL;
  }
  if (g_nvidia_gpu_runtime_handle != (dcgmHandle_t)0) {
    if (g_nvidia_gpu_runtime_remote)
      (void)dcgmDisconnect(g_nvidia_gpu_runtime_handle);
#if !defined(MONITOR_CPU_BACKEND_DCGM)
    else
      (void)dcgmStopEmbedded(g_nvidia_gpu_runtime_handle);
#endif
  }
#if !defined(MONITOR_CPU_BACKEND_DCGM)
  (void)dcgmShutdown();
#endif
  g_nvidia_gpu_runtime_handle = (dcgmHandle_t)0;
  g_nvidia_gpu_runtime_remote = 0;
  g_nvidia_gpu_runtime_ndev = 0;
  g_nvidia_gpu_runtime_watch_profile = -1;
  g_nvidia_gpu_runtime_ready = 0;
  g_nvidia_gpu_warmup_done = 0;
  g_nvidia_gpu_warmup_profile = -1;
}

int nvidia_gpu_runtime_prepare(int *fail_stage)
{
  dcgmReturn_t rc;
  int i;
  int watch_profile = 0;
  char group_name[] = "gpu_all";

  if (g_nvidia_gpu_runtime_ready)
    return 0;

#ifdef MONITOR_GPU_DCGM_DLOPEN
  if (dcgm_gpu_dyn_load() < 0) {
    *fail_stage = NVIDIA_GPU_FAIL_DCGM_INIT;
    ERROR("DCGM runtime load failed: %s\n", dcgm_gpu_dyn_last_error());
    return -1;
  }
#endif

  rc = dcgmInit();
  if (rc != DCGM_ST_OK) {
    *fail_stage = NVIDIA_GPU_FAIL_DCGM_INIT;
    ERROR("DCGM init failed: %s\n", dcgm_err(rc));
    return -1;
  }
  rc = monitor_dcgm_attach_for_process(&g_nvidia_gpu_runtime_handle, &g_nvidia_gpu_runtime_remote);
  if (rc != DCGM_ST_OK || g_nvidia_gpu_runtime_handle == (dcgmHandle_t)0) {
    *fail_stage = NVIDIA_GPU_FAIL_ATTACH;
    ERROR("DCGM attach failed (embedded or 127.0.0.1 hostengine): %s%s\n", dcgm_err(rc),
          rc == DCGM_ST_CONNECTION_NOT_VALID ? " (start nv-hostengine on this node?)" : "");
    nvidia_gpu_runtime_cleanup();
    return -1;
  }
  rc = nvidia_gpu_discover_gpu_ids(g_nvidia_gpu_runtime_handle, g_nvidia_gpu_runtime_gpu_ids,
                                   &g_nvidia_gpu_runtime_ndev);
  if (rc != DCGM_ST_OK) {
    *fail_stage = NVIDIA_GPU_FAIL_DISCOVERY;
    ERROR("DCGM list devices failed: %s\n", dcgm_err(rc));
    nvidia_gpu_runtime_cleanup();
    return -1;
  }
  if (g_nvidia_gpu_runtime_ndev <= 0) {
    ERROR("DCGM reports no supported GPUs\n");
    nvidia_gpu_runtime_cleanup();
    return -1;
  }

  rc = dcgmGroupCreate(g_nvidia_gpu_runtime_handle, DCGM_GROUP_EMPTY, group_name,
                       &g_nvidia_gpu_runtime_group);
  if (rc != DCGM_ST_OK) {
    *fail_stage = NVIDIA_GPU_FAIL_GROUP_CREATE;
    ERROR("DCGM group creation failed: %s\n", dcgm_err(rc));
    nvidia_gpu_runtime_cleanup();
    return -1;
  }
  for (i = 0; i < g_nvidia_gpu_runtime_ndev; i++) {
    rc = dcgmGroupAddDevice(g_nvidia_gpu_runtime_handle, g_nvidia_gpu_runtime_group,
                            g_nvidia_gpu_runtime_gpu_ids[i]);
    if (rc != DCGM_ST_OK) {
      *fail_stage = NVIDIA_GPU_FAIL_GROUP_ADD_DEVICE;
      ERROR("DCGM group add device gpu_id=%u failed: %s\n", g_nvidia_gpu_runtime_gpu_ids[i],
            dcgm_err(rc));
      nvidia_gpu_runtime_cleanup();
      return -1;
    }
  }

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
        nf = (unsigned int)NVIDIA_GPU_NFIELDS;
        fid = g_dcgm_field_ids;
        profile_name = "full-prof";
      } else if (attempt == 1) {
        nf = (unsigned int)NVIDIA_GPU_DCGM_NCORE;
        fid = g_dcgm_field_ids_core;
        profile_name = "core-prof";
      } else {
        nf = (unsigned int)NVIDIA_GPU_DCGM_NBASIC;
        fid = g_dcgm_field_ids_basic;
        profile_name = "basic-nonprof";
      }

      rc = dcgmFieldGroupCreate(g_nvidia_gpu_runtime_handle, nf, (unsigned short *)fid,
                                (char *)"hpcperfstats_fields", &g_nvidia_gpu_runtime_field_group);
      if (rc != DCGM_ST_OK) {
        *fail_stage = NVIDIA_GPU_FAIL_FIELD_GROUP_CREATE;
        if (attempt == 2)
          ERROR("DCGM field group creation failed: %s\n", dcgm_err(rc));
        else
          TRACE("DCGM field group creation failed for %s (will retry fallback): %s\n", profile_name,
                dcgm_err(rc));
        continue;
      }

      rc = dcgmWatchFields(g_nvidia_gpu_runtime_handle, g_nvidia_gpu_runtime_group,
                           g_nvidia_gpu_runtime_field_group, 10000000, 3600.0, 3600);
      if (rc != DCGM_ST_OK) {
        *fail_stage = NVIDIA_GPU_FAIL_WATCH_FIELDS;
        if (attempt == 2)
          ERROR("DCGM watch fields failed: %s\n", dcgm_err(rc));
        else
          TRACE("DCGM watch fields failed for %s (will retry fallback): %s\n", profile_name,
                dcgm_err(rc));
        (void)dcgmFieldGroupDestroy(g_nvidia_gpu_runtime_handle, g_nvidia_gpu_runtime_field_group);
        g_nvidia_gpu_runtime_field_group = (dcgmFieldGrp_t)NULL;
        continue;
      }
      watch_profile = attempt;
      g_last_watch_profile = watch_profile;
      break;
    }
    if (rc != DCGM_ST_OK || g_nvidia_gpu_runtime_field_group == (dcgmFieldGrp_t)NULL) {
      nvidia_gpu_runtime_cleanup();
      return -1;
    }
  }
  if (watch_profile > 0) {
    monitor_log_warn("nvidia_gpu: using DCGM fallback watch profile %s\n",
                     watch_profile == 1 ? "core-prof" : "basic-nonprof");
  }
  if (nvidia_gpu_maybe_warmup(g_nvidia_gpu_runtime_handle, g_nvidia_gpu_runtime_group,
                              g_nvidia_gpu_runtime_field_group, watch_profile) < 0) {
    nvidia_gpu_runtime_cleanup();
    return -1;
  }
  g_nvidia_gpu_runtime_watch_profile = watch_profile;
  g_nvidia_gpu_runtime_ready = 1;
  return 0;
}
