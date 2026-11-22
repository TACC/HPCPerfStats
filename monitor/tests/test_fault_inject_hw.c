/* Fault-injection matrix for the hardware fault points (built with -DDEBUG in every build):
 * perf_open (EBUSY PMU_BUSY, EACCES PERMISSION_DENIED), mad_query (BACKEND_ERROR, then BACKOFF
 * once the fail streak triggers), dcgm_fetch (BACKEND_ERROR on every GPU row) and hwmon_read
 * (READ_FAILED), plus a failed host_ib port walk (opendir errno code on every row), a DCGM
 * fetch that fails from the first cycle (rows still appear) and nvml_fetch (BACKEND_ERROR for one
 * cycle, then the DCGM path serves the GPU). Each is forced through the debug_fault_inject table
 * over the existing fakes (fake perf, MAD, NVML and DCGM test hooks, a fake hwmon tree), lands on
 * exactly its keys, and recovers after release; then every registered failure site in this binary
 * must have fired. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "arm_core_perf.h"
#include "arm_power_hwmon.h"
#include "arm_power_socket.h"
#include "collect.h"
#include "collect_tier.h"
#include "dcgm_gpu_dyn.h"
#include "dict.h"
#include "ib.h"
#include "ib_common.h"
#include "ib_family.h"
#include "ib_mad.h"
#include "ib_mad_dyn.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel_debug.h"
#include "test_fake_perf.h"

/* host_ib schema, taken before nvidia_gpu.h (via the NVML headers) redefines KEYS. */
#define X SCHEMA_DEF
static const char g_ib_def[] = JOIN(KEYS);
#undef X
#undef KEYS

#include "nvidia_gpu_dcgm_watch.h"
#include "nvidia_gpu_nvml.h"
#include "nvml.h"

extern struct stats_type nvidia_gpu_stats_type;

static struct stats_type g_ib;
static struct stats_type g_power;
struct stats_type *const stats_type_table[] = {&g_ib, &g_power, &nvidia_gpu_stats_type};
const size_t stats_type_nr = sizeof(stats_type_table) / sizeof(stats_type_table[0]);

/* Link stub: the host_ib sysfs side is outside this test. */
int path_collect_key_value_dir(const char *dir_path, struct stats *stats)
{
  (void)dir_path;
  stats_set(stats, "port_rcv_errors", 3);
  return 0;
}

static int g_ib_walk_errno; /* nonzero: /sys/class/infiniband fails to open with it */

int ib_foreach_hca_port(ib_hca_port_fn fn, void *ctx)
{
  if (g_ib_walk_errno != 0) {
    errno = g_ib_walk_errno;
    return -1;
  }
  fn("mlx5_0", 1, ctx);
  return 0;
}

static unsigned int state(struct stats *s, const char *key)
{
  int i = schema_ref(&s->s_type->st_schema, key);

  assert(i >= 0);
  return s->s_val_state[i];
}

static char g_root[512];

static void test_perf_open(void)
{
  char dev[600], midr[600], cpuinfo[600];
  struct arm_core_perf_cfg c;
  struct arm_core_counts m;

  fake_sysfs_init(g_root, sizeof(g_root));
  snprintf(dev, sizeof(dev), "%s/devices", g_root);
  snprintf(midr, sizeof(midr), "%s/midr_el1", g_root);
  snprintf(cpuinfo, sizeof(cpuinfo), "%s/cpuinfo", g_root);
  fake_sysfs_write(g_root, "devices/armv8_pmuv3_0/type", "8\n");
  fake_sysfs_write(g_root, "devices/armv8_pmuv3_0/format/event", "config:0-15\n");
  fake_sysfs_write(g_root, "devices/armv8_pmuv3_0/events/cpu_cycles", "event=0x0011\n");
  fake_sysfs_write(g_root, "devices/armv8_pmuv3_0/events/inst_retired", "event=0x0008\n");
  fake_sysfs_write(g_root, "midr_el1", "0x00000000410fd4f0\n");
  memset(&c, 0, sizeof(c));
  c.devices_dir = dev;
  c.midr_path = midr;
  c.cpuinfo_path = cpuinfo;
  c.open_fn = fake_perf_open;

  assert(stats_fault_inject_set("perf_open:fp_sp_spec!16,perf_open:fp_dp_spec!13") == 2);
  assert(arm_core_perf_begin(1, &c) == 0);
  arm_core_perf_collect_cpu(0, &m);
  assert(m.code[ARM_CORE_FP_SP] == STATS_SENTINEL_PMU_BUSY && !m.have[ARM_CORE_FP_SP]);
  assert(m.code[ARM_CORE_FP_DP] == STATS_SENTINEL_PERMISSION_DENIED && !m.have[ARM_CORE_FP_DP]);
  assert(m.have[ARM_CORE_CYC] && m.have[ARM_CORE_FP_HP]); /* only the two forced events */
  arm_core_perf_cleanup();
  fake_perf_reset();

  /* Opens happen at begin / SIGHUP: after release the next begin opens both. */
  assert(stats_fault_inject_set(NULL) == 0);
  assert(arm_core_perf_begin(1, &c) == 0);
  arm_core_perf_collect_cpu(0, &m);
  assert(m.have[ARM_CORE_FP_SP] && m.have[ARM_CORE_FP_DP]);
  arm_core_perf_cleanup();
  fake_perf_reset();
  fake_sysfs_rm(g_root);
}

static int g_mad_port;

static struct ibmad_port *fake_open(char *dev, int port, int *classes, int n)
{
  (void)dev;
  (void)port;
  (void)classes;
  (void)n;
  return (struct ibmad_port *)&g_mad_port;
}

static void fake_close(struct ibmad_port *p)
{
  (void)p;
}

static uint8_t *fake_pma(void *buf, ib_portid_t *dest, int port, unsigned timeout, unsigned id,
                         const struct ibmad_port *src)
{
  (void)dest;
  (void)port;
  (void)timeout;
  (void)id;
  (void)src;
  return (uint8_t *)buf;
}

/* A switch answers PortInfo behind the fake port. */
static uint8_t *fake_smp(void *buf, ib_portid_t *id, unsigned attr, unsigned mod, unsigned timeout,
                         const struct ibmad_port *src)
{
  (void)id;
  (void)attr;
  (void)mod;
  (void)timeout;
  (void)src;
  return (uint8_t *)buf;
}

/* Fields decode as 0, sized like the locals ib_mad.c decodes into: the switch LID and port are
 * int, the switch counters uint64_t. */
static void fake_decode(uint8_t *buf, enum MAD_FIELDS field, void *val)
{
  (void)buf;
  if (field == IB_PORT_LID_F || field == IB_PORT_LOCAL_PORT_F)
    *(int *)val = 0;
  else
    *(uint64_t *)val = 0;
}

static void ib_tick(void)
{
  stats_type_mark_not_updated(&g_ib);
  ib_family_collect(&g_ib);
  stats_fault_inject_tick();
}

static void test_mad_query(void)
{
  struct ib_mad_dyn_test_hooks hooks;
  struct stats *s;

  memset(&hooks, 0, sizeof(hooks));
  hooks.mad_rpc_open_port = fake_open;
  hooks.mad_rpc_close_port = fake_close;
  hooks.pma_query_via = fake_pma;
  hooks.smp_query_via = fake_smp;
  hooks.mad_decode_field = fake_decode;
  ib_mad_dyn_test_set_hooks(&hooks);
  ib_mad_test_reset_backoff();

  ib_tick();
  s = get_current_stats(&g_ib, "mlx5_0.1");
  assert(state(s, "sw_rx_bytes") == STATS_SENTINEL_OK);

  assert(stats_fault_inject_set("mad_query:mlx5_0") == 1);
  ib_tick();
  assert(state(s, "sw_rx_bytes") == STATS_SENTINEL_BACKEND_ERROR);
  assert(state(s, "sw_tx_packets") == STATS_SENTINEL_BACKEND_ERROR);
  assert(state(s, "port_rcv_errors") == STATS_SENTINEL_OK); /* sysfs key untouched */
  for (int i = 0; i < 8; i++)
    ib_tick();
  assert(state(s, "sw_rx_bytes") == STATS_SENTINEL_BACKOFF); /* fail streak hit */
  assert(state(s, "port_rcv_errors") == STATS_SENTINEL_OK);

  assert(stats_fault_inject_set(NULL) == 0);
  ib_mad_test_reset_backoff();
  ib_tick();
  assert(state(s, "sw_rx_bytes") == STATS_SENTINEL_OK);

  /* Regression: a failed port walk left every host_ib key NOT_UPDATED (no code). */
  g_ib_walk_errno = EIO;
  ib_tick();
  assert(state(s, "port_rcv_errors") == STATS_SENTINEL_READ_FAILED);
  assert(state(s, "sw_rx_bytes") == STATS_SENTINEL_READ_FAILED);
  g_ib_walk_errno = 0;
  ib_tick();
  assert(state(s, "port_rcv_errors") == STATS_SENTINEL_OK);
  ib_mad_test_reset_backoff();
  ib_mad_dyn_test_set_hooks(NULL);
}

static void add_meter(const char *root, int idx, const char *label, long long uw)
{
  char path[600];
  FILE *f;

  snprintf(path, sizeof(path), "class/hwmon/hwmon%d/power1_oem_info", idx);
  fake_sysfs_write(root, path, label);
  snprintf(path, sizeof(path), "%s/class/hwmon/hwmon%d/power1_average", root, idx);
  f = fopen(path, "w");
  assert(f != NULL);
  fprintf(f, "%lld\n", uw);
  fclose(f);
}

static void test_hwmon_read(void)
{
  struct arm_power_hwmon h;
  int slots[ARM_POWER_KIND_COUNT];
  struct stats *s;

  fake_sysfs_init(g_root, sizeof(g_root));
  add_meter(g_root, 0, "CPU Power Socket 0", 100000000);
  add_meter(g_root, 1, "Grace Power Socket 0", 250000000);
  assert(arm_power_hwmon_open(&h, g_root) == 2);
  for (int k = 0; k < ARM_POWER_KIND_COUNT; k++)
    slots[k] = arm_power_hwmon_find(&h, k, 0);
  s = get_current_stats(&g_power, "0");

  assert(stats_fault_inject_set("hwmon_read:Grace@1") == 1);
  stats_type_mark_not_updated(&g_power);
  arm_power_socket_publish(s, &h, slots);
  stats_fault_inject_tick();
  assert(state(s, "socket_power_w") == STATS_SENTINEL_READ_FAILED);
  assert(state(s, "cpu_power_w") == STATS_SENTINEL_OK);

  stats_type_mark_not_updated(&g_power);
  arm_power_socket_publish(s, &h, slots); /* released after one cycle */
  assert(state(s, "socket_power_w") == STATS_SENTINEL_OK);
  arm_power_hwmon_close(&h);
  fake_sysfs_rm(g_root);
}

static dcgmReturn_t ok0(void)
{
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_start(dcgmOperationMode_t mode, dcgmHandle_t *h)
{
  (void)mode;
  *h = (dcgmHandle_t)0x1234;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_start_v2(dcgmStartEmbeddedV2Params_v1 *p)
{
  p->dcgmHandle = (dcgmHandle_t)0x1234;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_handle_only(dcgmHandle_t h)
{
  (void)h;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_devices(dcgmHandle_t h, unsigned int ids[], int *count)
{
  (void)h;
  ids[0] = 0;
  *count = 1;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_entities(dcgmHandle_t h, dcgm_field_entity_group_t g, dcgm_field_eid_t e[],
                                  int *n, unsigned int flags)
{
  (void)h;
  (void)g;
  (void)e;
  (void)flags;
  *n = 0;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_group_create(dcgmHandle_t h, dcgmGroupType_t t, char *name,
                                      dcgmGpuGrp_t *g)
{
  (void)h;
  (void)t;
  (void)name;
  *g = (dcgmGpuGrp_t)7;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_group_destroy(dcgmHandle_t h, dcgmGpuGrp_t g)
{
  (void)h;
  (void)g;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_group_add(dcgmHandle_t h, dcgmGpuGrp_t g, unsigned int id)
{
  (void)h;
  (void)g;
  (void)id;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_fg_create(dcgmHandle_t h, int n, unsigned short *ids, char *name,
                                   dcgmFieldGrp_t *fg)
{
  (void)h;
  (void)n;
  (void)ids;
  (void)name;
  *fg = (dcgmFieldGrp_t)9;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_fg_destroy(dcgmHandle_t h, dcgmFieldGrp_t fg)
{
  (void)h;
  (void)fg;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_watch(dcgmHandle_t h, dcgmGpuGrp_t g, dcgmFieldGrp_t fg, long long f,
                               double age, int n)
{
  (void)h;
  (void)g;
  (void)fg;
  (void)f;
  (void)age;
  (void)n;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_update(dcgmHandle_t h, int wait)
{
  (void)h;
  (void)wait;
  return DCGM_ST_OK;
}

/* GPU util 42, power usage NOT_SUPPORTED and a blank temperature (the per-field miss list). */
static dcgmReturn_t fake_latest(dcgmHandle_t h, dcgmGpuGrp_t g, dcgmFieldGrp_t fg,
                                dcgmFieldValueEnumeration_f cb, void *user)
{
  dcgmFieldValue_v1 v[3];

  (void)h;
  (void)g;
  (void)fg;
  memset(v, 0, sizeof(v));
  v[0].fieldId = DCGM_FI_DEV_GPU_UTIL;
  v[0].fieldType = DCGM_FT_INT64;
  v[0].status = DCGM_ST_OK;
  v[0].value.i64 = 42;
  v[1].fieldId = DCGM_FI_DEV_POWER_USAGE;
  v[1].fieldType = DCGM_FT_DOUBLE;
  v[1].status = DCGM_ST_NOT_SUPPORTED;
  v[2].fieldId = DCGM_FI_DEV_GPU_TEMP;
  v[2].fieldType = DCGM_FT_INT64;
  v[2].status = DCGM_ST_OK;
  v[2].value.i64 = 0x7ffffffffffffff0LL; /* DCGM_INT64_BLANK */
  cb(0, v, 3, user);
  return DCGM_ST_OK;
}

static void gpu_tick(void)
{
  stats_type_mark_not_updated(&nvidia_gpu_stats_type);
  nvidia_gpu_stats_type.st_collect(&nvidia_gpu_stats_type);
  stats_fault_inject_tick();
}

static void set_dcgm_hooks(struct dcgm_gpu_dyn_test_hooks *hooks)
{
  memset(hooks, 0, sizeof(*hooks));
  hooks->dcgmInit = ok0;
  hooks->dcgmShutdown = ok0;
  hooks->dcgmStartEmbedded = fake_start;
  hooks->dcgmStartEmbedded_v2 = fake_start_v2;
  hooks->dcgmStopEmbedded = fake_handle_only;
  hooks->dcgmDisconnect = fake_handle_only;
  hooks->dcgmGetAllDevices = fake_devices;
  hooks->dcgmGetAllSupportedDevices = fake_devices;
  hooks->dcgmGetEntityGroupEntities = fake_entities;
  hooks->dcgmGroupCreate = fake_group_create;
  hooks->dcgmGroupDestroy = fake_group_destroy;
  hooks->dcgmGroupAddDevice = fake_group_add;
  hooks->dcgmFieldGroupCreate = fake_fg_create;
  hooks->dcgmFieldGroupDestroy = fake_fg_destroy;
  hooks->dcgmWatchFields = fake_watch;
  hooks->dcgmUpdateAllFields = fake_update;
  hooks->dcgmGetLatestValues = fake_latest;
  dcgm_gpu_dyn_test_set_hooks(hooks);
}

/* Regression: a fetch failing from the first cycle marked an empty table, so the node showed no
 * GPU at all instead of BACKEND_ERROR rows. Runs before any row exists. */
static void test_dcgm_fetch_fails_at_start(void)
{
  struct dcgm_gpu_dyn_test_hooks hooks;
  size_t i = 0;
  char *key;

  set_dcgm_hooks(&hooks);
  assert(stats_fault_inject_set("dcgm_fetch@1") == 1);
  gpu_tick();
  key = dict_for_each(&nvidia_gpu_stats_type.st_current_dict, &i);
  assert(key != NULL && strcmp(key_to_stats(key)->s_dev, "0") == 0);
  assert(dict_for_each(&nvidia_gpu_stats_type.st_current_dict, &i) == NULL);
  for (size_t k = 0; k < nvidia_gpu_stats_type.st_schema.sc_len; k++)
    assert(key_to_stats(key)->s_val_state[k] == STATS_SENTINEL_BACKEND_ERROR);
  dcgm_gpu_dyn_test_set_hooks(NULL);
}

static void test_dcgm_fetch(void)
{
  struct dcgm_gpu_dyn_test_hooks hooks;
  struct stats *gpu;

  set_dcgm_hooks(&hooks);
  gpu_tick();
  gpu = get_current_stats(&nvidia_gpu_stats_type, "0");
  assert(state(gpu, "gpu_util") == STATS_SENTINEL_OK);
  assert(gpu->s_val[schema_ref(&nvidia_gpu_stats_type.st_schema, "gpu_util")] == 42);
  assert(state(gpu, "power_usage") == STATS_SENTINEL_NOT_PRESENT);
  assert(state(gpu, "temperature") == STATS_SENTINEL_INVALID_VALUE);

  assert(stats_fault_inject_set("dcgm_fetch@1") == 1);
  gpu_tick();
  for (size_t k = 0; k < nvidia_gpu_stats_type.st_schema.sc_len; k++)
    assert(gpu->s_val_state[k] == STATS_SENTINEL_BACKEND_ERROR);

  gpu_tick(); /* released: the runtime re-prepares and the row reads again */
  assert(state(gpu, "gpu_util") == STATS_SENTINEL_OK);
  dcgm_gpu_dyn_test_set_hooks(NULL);
}

/* Stub NVML API serving one GPM-capable GPU. */
static int g_nvml_sample_gets;
static int g_nvml_allocs;
static int g_nvml_sample_slots[2];

static nvidia_gpu_nvml_ret_t nvml_ok(void)
{
  return 0;
}
static const char *nvml_err(nvidia_gpu_nvml_ret_t r)
{
  (void)r;
  return "stub error";
}
static nvidia_gpu_nvml_ret_t nvml_count(unsigned int *n)
{
  *n = 1;
  return 0;
}
static nvidia_gpu_nvml_ret_t nvml_by_index(unsigned int i, nvidia_gpu_nvml_dev_t *d)
{
  *d = (nvidia_gpu_nvml_dev_t)(uintptr_t)(i + 1);
  return 0;
}
static nvidia_gpu_nvml_ret_t nvml_clock(nvidia_gpu_nvml_dev_t d, int type, unsigned int *mhz)
{
  (void)d;
  (void)type;
  *mhz = 1980;
  return 0;
}
static nvidia_gpu_nvml_ret_t nvml_power(nvidia_gpu_nvml_dev_t d, unsigned int *mw)
{
  (void)d;
  *mw = 151000;
  return 0;
}
static nvidia_gpu_nvml_ret_t nvml_util(nvidia_gpu_nvml_dev_t d, void *u)
{
  nvmlUtilization_t *util = u;

  (void)d;
  util->gpu = 97;
  util->memory = 41;
  return 0;
}
static nvidia_gpu_nvml_ret_t nvml_mem(nvidia_gpu_nvml_dev_t d, void *m)
{
  nvmlMemory_t *mem = m;

  (void)d;
  mem->total = 1048576ULL * 1000;
  mem->used = 1048576ULL * 600;
  mem->free = mem->total - mem->used;
  return 0;
}
static nvidia_gpu_nvml_ret_t nvml_gpm_support(nvidia_gpu_nvml_dev_t d, void *s)
{
  (void)d;
  ((nvmlGpmSupport_t *)s)->isSupportedDevice = 1;
  return 0;
}
static nvidia_gpu_nvml_ret_t nvml_alloc(nvidia_gpu_nvml_sample_t *s)
{
  *s = &g_nvml_sample_slots[g_nvml_allocs++ % 2];
  return 0;
}
static nvidia_gpu_nvml_ret_t nvml_free(nvidia_gpu_nvml_sample_t s)
{
  (void)s;
  return 0;
}
static nvidia_gpu_nvml_ret_t nvml_sample_get(nvidia_gpu_nvml_dev_t d, nvidia_gpu_nvml_sample_t s)
{
  (void)d;
  (void)s;
  g_nvml_sample_gets++;
  return 0;
}
static nvidia_gpu_nvml_ret_t nvml_metrics_get(void *p)
{
  nvmlGpmMetricsGet_t *mg = p;

  for (unsigned int m = 0; m < mg->numMetrics; m++) {
    mg->metrics[m].nvmlReturn = 0;
    mg->metrics[m].value = 50.0;
  }
  return 0;
}

static const struct nvidia_gpu_nvml_api g_nvml_api = {
    .init = nvml_ok,
    .shutdown = nvml_ok,
    .error_string = nvml_err,
    .device_count = nvml_count,
    .device_by_index = nvml_by_index,
    .clock_info = nvml_clock,
    .power_usage = nvml_power,
    .utilization = nvml_util,
    .memory_info = nvml_mem,
    .gpm_query_support = nvml_gpm_support,
    .gpm_sample_alloc = nvml_alloc,
    .gpm_sample_free = nvml_free,
    .gpm_sample_get = nvml_sample_get,
    .gpm_metrics_get = nvml_metrics_get,
};

/* The NVML path closes a GPM window only once NVIDIA_GPU_NVML_GPM_MIN_GAP_US has passed. */
static void gpu_tick_after_gpm_gap(void)
{
  usleep((useconds_t)(NVIDIA_GPU_NVML_GPM_MIN_GAP_US + 10000));
  gpu_tick();
}

/* Regression: no fault point covered the NVML+GPM source, so on GPM-capable GPUs the live fault
 * layer injected no GPU failure. A forced nvml_fetch takes the real failure path: the cycle marks
 * every GPU key BACKEND_ERROR, NVML is given up for the process, and the next collect reads the
 * DCGM path (GPM-only tensor_dmma_active NOT_PRESENT there). Runs last: NVML stays given up. */
static void test_nvml_fetch_falls_back_to_dcgm(void)
{
  struct dcgm_gpu_dyn_test_hooks hooks;
  struct stats *gpu;
  int gets;

  set_dcgm_hooks(&hooks);
  nvidia_gpu_runtime_cleanup(); /* a node on NVML+GPM never prepared the DCGM path */
  nvidia_gpu_nvml_test_set_api(&g_nvml_api);
  assert(nvidia_gpu_stats_type.st_begin(&nvidia_gpu_stats_type) == 0);
  gpu_tick_after_gpm_gap();
  gpu = get_current_stats(&nvidia_gpu_stats_type, "0");
  assert(state(gpu, "gpu_util") == STATS_SENTINEL_OK);
  assert(gpu->s_val[schema_ref(&nvidia_gpu_stats_type.st_schema, "gpu_util")] == 97);
  assert(state(gpu, "tensor_dmma_active") == STATS_SENTINEL_OK);

  assert(stats_fault_inject_set("nvml_fetch@1") == 1);
  gpu_tick_after_gpm_gap();
  assert(stats_fault_inject_fired(0) == 1);
  for (size_t k = 0; k < nvidia_gpu_stats_type.st_schema.sc_len; k++)
    assert(gpu->s_val_state[k] == STATS_SENTINEL_BACKEND_ERROR);

  gets = g_nvml_sample_gets;
  assert(nvidia_gpu_stats_type.st_begin(&nvidia_gpu_stats_type) == 0); /* no NVML retry */
  gpu_tick();
  assert(g_nvml_sample_gets == gets);
  assert(state(gpu, "gpu_util") == STATS_SENTINEL_OK);
  assert(gpu->s_val[schema_ref(&nvidia_gpu_stats_type.st_schema, "gpu_util")] == 42);
  assert(state(gpu, "tensor_dmma_active") == STATS_SENTINEL_NOT_PRESENT);
  assert(stats_fault_inject_set(NULL) == 0);
  nvidia_gpu_nvml_test_set_api(NULL);
  dcgm_gpu_dyn_test_set_hooks(NULL);
}

static void init_type(struct stats_type *t, const char *name, const char *def)
{
  memset(t, 0, sizeof(*t));
  snprintf(t->st_name, sizeof(t->st_name), "%s", name);
  t->st_schema_def = (char *)def;
  t->st_enabled = 1;
}

int main(void)
{
  size_t never;

  init_type(&g_ib, "host_ib", g_ib_def);
  init_type(&g_power, "host_power_socket",
            "cpu_power_w socket_power_w sysio_power_w module_power_w");
  for (size_t i = 0; i < stats_type_nr; i++) {
    assert(stats_type_init(stats_type_table[i]) == 0);
    collect_tier_apply_to_type(stats_type_table[i]);
    stats_type_table[i]->st_enabled = 1;
  }

  test_perf_open();
  test_mad_query();
  test_hwmon_read();
  test_dcgm_fetch_fails_at_start();
  test_dcgm_fetch();
  test_nvml_fetch_falls_back_to_dcgm();

  never = stats_sentinel_sites_report(NULL);
  if (never != 0) {
    fprintf(stderr, "test_fault_inject_hw: %zu failure site(s) never fired:\n", never);
    (void)stats_sentinel_sites_report(stderr);
  }
  assert(never == 0);
  for (size_t i = 0; i < stats_type_nr; i++)
    stats_type_destroy(stats_type_table[i]);
  printf("test_fault_inject_hw passed\n");
  return 0;
}
