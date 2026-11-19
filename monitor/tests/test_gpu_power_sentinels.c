/* GPU and power value sentinels: DCGM per-field status (NOT_SUPPORTED NOT_PRESENT, other
 * READ_FAILED) recorded on the row; intel_x86_rapl / amd_x86_rapl begin gates (supported
 * processor, a readable domain) and per-domain publish of each x86_rapl sample (value, or its
 * code; AMD publishes package and core only); hwmon meter read failure READ_FAILED (no published
 * 0). x86_rapl itself is stubbed (its reads are test_x86_rapl); real stats.c state bytes. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "arm_power_hwmon.h"
#include "collect_tier.h"
#include "nvidia_gpu_dcgm_field.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel.h"
#include "x86_rapl.h"

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;
extern struct stats_type intel_rapl_stats_type;
extern struct stats_type amd64_rapl_stats_type;
processor_t processor;

/* Fake x86_rapl: sockets begin reports, the domain mask it was asked for, and the samples. */
static struct x86_rapl_socket g_sockets[2] = {{.id = 0}, {.id = 1}};
static int g_rapl_sockets;
static unsigned g_rapl_domains;
static perf_open_fn g_rapl_open;
static int g_rapl_ends;
static struct x86_rapl_sample g_sample[X86_RAPL_DOMAINS];

int x86_rapl_begin(struct x86_rapl *r, const char *sysfs_root, unsigned domains,
                   perf_open_fn open_fn)
{
  assert(strcmp(sysfs_root, X86_RAPL_SYSFS_ROOT) == 0);
  g_rapl_open = open_fn;
  g_rapl_domains = domains;
  r->sockets = g_sockets;
  r->n_sockets = g_rapl_sockets;
  return g_rapl_sockets;
}

void x86_rapl_read(struct x86_rapl *r, int idx, struct x86_rapl_sample out[X86_RAPL_DOMAINS])
{
  (void)r;
  (void)idx;
  memcpy(out, g_sample, sizeof(g_sample));
}

void x86_rapl_end(struct x86_rapl *r)
{
  r->sockets = NULL;
  r->n_sockets = 0;
  g_rapl_ends++;
}

static void set_sample(enum x86_rapl_domain d, uint64_t mj, enum stats_sentinel status)
{
  g_sample[d].mj = mj;
  g_sample[d].status = status;
}

static unsigned int state_of(struct stats *s, const char *key)
{
  int i = schema_ref(&s->s_type->st_schema, key);

  assert(i >= 0);
  return s->s_val_state[i];
}

static void test_dcgm_field_status(void)
{
  dcgm_data_t row;

  assert(nvidia_gpu_dcgm_status_sentinel(DCGM_ST_NOT_SUPPORTED) == STATS_SENTINEL_NOT_PRESENT);
  assert(nvidia_gpu_dcgm_status_sentinel(DCGM_ST_NO_DATA) == STATS_SENTINEL_READ_FAILED);
  assert(strcmp(nvidia_gpu_dcgm_field_key(DCGM_FI_PROF_PIPE_FP64_ACTIVE), "fp64_active") == 0);
  assert(strcmp(nvidia_gpu_dcgm_field_key(DCGM_FI_DEV_POWER_USAGE), "power_usage") == 0);
  assert(strcmp(nvidia_gpu_dcgm_field_key(DCGM_FI_PROF_NVLINK_RX_BYTES), "gpu_nvlink_rx_bytes") ==
         0);
  assert(nvidia_gpu_dcgm_field_key(0) == NULL);

  memset(&row, 0, sizeof(row));
  nvidia_gpu_row_mark(&row, "fp64_active", STATS_SENTINEL_NOT_PRESENT);
  nvidia_gpu_row_mark(&row, NULL, STATS_SENTINEL_READ_FAILED);
  nvidia_gpu_row_mark(NULL, "x", STATS_SENTINEL_READ_FAILED);
  assert(row.nmiss == 1 && row.miss[0].code == STATS_SENTINEL_NOT_PRESENT);
  for (int i = 0; i < NVIDIA_GPU_ROW_MISS_MAX + 4; i++)
    nvidia_gpu_row_mark(&row, "gpu_util", STATS_SENTINEL_READ_FAILED);
  assert(row.nmiss == NVIDIA_GPU_ROW_MISS_MAX);
}

static void rapl_init(struct stats_type *t)
{
  assert(stats_type_init(t) == 0);
  t->st_enabled = 1;
}

/* Begin disables the type on an unsupported processor (without probing) or when no domain is
 * readable (state released); otherwise it asks x86_rapl for this vendor's domains. */
static void test_rapl_begin_gates(void)
{
  g_rapl_sockets = 2;
  processor = AMD_GENOA;
  g_rapl_domains = 0;
  assert(intel_rapl_stats_type.st_begin(&intel_rapl_stats_type) == -1);
  assert(!intel_rapl_stats_type.st_enabled && g_rapl_domains == 0);
  processor = SAPPHIRE_RAPIDS;
  assert(amd64_rapl_stats_type.st_begin(&amd64_rapl_stats_type) == -1);
  assert(!amd64_rapl_stats_type.st_enabled && g_rapl_domains == 0);

  intel_rapl_stats_type.st_enabled = 1;
  assert(intel_rapl_stats_type.st_begin(&intel_rapl_stats_type) == 0);
  assert(intel_rapl_stats_type.st_enabled && g_rapl_open == perf_open_cpu);
  assert(g_rapl_domains == (X86_RAPL_BIT(X86_RAPL_PKG) | X86_RAPL_BIT(X86_RAPL_CORES) |
                            X86_RAPL_BIT(X86_RAPL_GPU) | X86_RAPL_BIT(X86_RAPL_RAM)));
  processor = AMD_TURIN;
  amd64_rapl_stats_type.st_enabled = 1;
  assert(amd64_rapl_stats_type.st_begin(&amd64_rapl_stats_type) == 0);
  assert(g_rapl_domains == (X86_RAPL_BIT(X86_RAPL_PKG) | X86_RAPL_BIT(X86_RAPL_CORES)));

  g_rapl_sockets = 0;
  g_rapl_ends = 0;
  assert(amd64_rapl_stats_type.st_begin(&amd64_rapl_stats_type) == -1);
  assert(!amd64_rapl_stats_type.st_enabled && g_rapl_ends == 1);
  amd64_rapl_stats_type.st_enabled = 1;
}

/* Every socket row carries each domain's value or its code (PERMISSION_DENIED stays distinct from
 * READ_FAILED; an absent domain is NOT_PRESENT), never a stale value. */
static void test_rapl_intel_publish(void)
{
  struct stats *s;

  g_rapl_sockets = 2;
  processor = ICELAKE_SERVER;
  assert(intel_rapl_stats_type.st_begin(&intel_rapl_stats_type) == 0);
  set_sample(X86_RAPL_PKG, 1000, STATS_SENTINEL_OK);
  set_sample(X86_RAPL_CORES, 0, STATS_SENTINEL_PERMISSION_DENIED);
  set_sample(X86_RAPL_GPU, 0, STATS_SENTINEL_NOT_PRESENT);
  set_sample(X86_RAPL_RAM, 50, STATS_SENTINEL_OK);
  stats_type_mark_not_updated(&intel_rapl_stats_type);
  intel_rapl_stats_type.st_collect(&intel_rapl_stats_type);
  s = get_current_stats(&intel_rapl_stats_type, "1");
  assert(state_of(s, "pkg_energy") == STATS_SENTINEL_OK);
  assert(s->s_val[schema_ref(&intel_rapl_stats_type.st_schema, "pkg_energy")] == 1000ULL);
  assert(state_of(s, "pp0_energy") == STATS_SENTINEL_PERMISSION_DENIED);
  assert(state_of(s, "pp1_energy") == STATS_SENTINEL_NOT_PRESENT);
  assert(state_of(s, "dram_energy") == STATS_SENTINEL_OK);

  set_sample(X86_RAPL_PKG, 0, STATS_SENTINEL_READ_FAILED);
  stats_type_mark_not_updated(&intel_rapl_stats_type);
  intel_rapl_stats_type.st_collect(&intel_rapl_stats_type);
  assert(state_of(s, "pkg_energy") == STATS_SENTINEL_READ_FAILED);
  assert(state_of(get_current_stats(&intel_rapl_stats_type, "0"), "pkg_energy") ==
         STATS_SENTINEL_READ_FAILED);
}

static void test_rapl_amd_publish(void)
{
  struct stats *s;

  g_rapl_sockets = 1;
  processor = AMD_MILAN;
  assert(amd64_rapl_stats_type.st_begin(&amd64_rapl_stats_type) == 0);
  set_sample(X86_RAPL_PKG, 0, STATS_SENTINEL_READ_FAILED);
  set_sample(X86_RAPL_CORES, 400, STATS_SENTINEL_OK);
  set_sample(X86_RAPL_RAM, 0, STATS_SENTINEL_PERMISSION_DENIED);
  stats_type_mark_not_updated(&amd64_rapl_stats_type);
  amd64_rapl_stats_type.st_collect(&amd64_rapl_stats_type);
  s = get_current_stats(&amd64_rapl_stats_type, "0");
  assert(state_of(s, "pkg_energy") == STATS_SENTINEL_READ_FAILED);
  assert(state_of(s, "core_energy") == STATS_SENTINEL_OK);
  assert(s->s_val[schema_ref(&amd64_rapl_stats_type.st_schema, "core_energy")] == 400ULL);
}

static void test_hwmon_read_failure(void)
{
  struct arm_power_hwmon h;
  double w = 5.0;
  int fds[2];

  memset(&h, 0, sizeof(h));
  assert(pipe(fds) == 0);
  close(fds[1]);
  h.n = 1;
  h.m[0].fd = fds[0]; /* pread on a pipe: ESPIPE */
  assert(arm_power_hwmon_read(&h, 0, &w) == STATS_SENTINEL_READ_FAILED && w == 0.0);
  assert(arm_power_hwmon_read(&h, 1, &w) == STATS_SENTINEL_NOT_PRESENT);
  close(fds[0]);
}

int main(void)
{
  rapl_init(&intel_rapl_stats_type);
  rapl_init(&amd64_rapl_stats_type);
  collect_tier_set_enabled(0);
  collect_tier_set_phase(COLLECT_FULL);

  test_dcgm_field_status();
  test_rapl_begin_gates();
  test_rapl_intel_publish();
  test_rapl_amd_publish();
  test_hwmon_read_failure();

  stats_type_destroy(&intel_rapl_stats_type);
  stats_type_destroy(&amd64_rapl_stats_type);
  printf("test_gpu_power_sentinels passed\n");
  return 0;
}
