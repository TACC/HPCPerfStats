/* Two-tier collection: slow-key table, enable flag, and runtime phase.
 *
 * Slow keys are assigned from a static (type,key) table plus an auto-rule for
 * any key ending in `_error`/`_errors`. The whole mechanism is inert unless
 * `enable_slow_tier` is on (default). Set `enable_slow_tier 0` to restore
 * legacy single-tier behavior. */
#include "collect_tier.h"

#include <stddef.h>
#include <string.h>

#include "stats.h"

static int g_slow_tier_enabled;
static enum collect_phase g_collect_phase = COLLECT_FULL;

/* Static slow-tier assignments, keyed by emitted (st_name, se_key). Keys ending
 * in `_error`/`_errors` are handled by collect_tier_key_has_error_suffix() and
 * need not be listed here. */
static const struct {
  const char *type;
  const char *key;
} collect_tier_slow_table[] = {
    {"host_lnet", "msgs_alloc_max"},
    {"host_lnet", "msgs_alloc"},
    {"host_lnet", "errors"},
    {"host_lnet", "rx_msgs_dropped"},
    {"host_lnet", "rx_bytes_dropped"},

    {"lustre_mdc", "ldlm_cancel"},
    {"lustre_mdc", "mds_close"},
    {"lustre_mdc", "mds_getattr"},
    {"lustre_mdc", "mds_getattr_lock"},
    {"lustre_mdc", "mds_getxattr"},
    {"lustre_mdc", "mds_readpage"},
    {"lustre_mdc", "mds_statfs"},
    {"lustre_mdc", "mds_sync"},

    {"host_mem", "anon_huge_pages"},
    {"host_mem", "anon_pages"},
    {"host_mem", "bounce"},
    {"host_mem", "dirty"},
    {"host_mem", "huge_pages_free"},
    {"host_mem", "huge_pages_total"},
    {"host_mem", "inactive"},
    {"host_mem", "mapped"},
    {"host_mem", "nfs_unstable"},
    {"host_mem", "page_tables"},
    {"host_mem", "writeback"},

    {"host_net", "collisions"},
    {"host_net", "rx_dropped"},
    {"host_net", "tx_dropped"},

    {"host_nfs", "xprt_bad_xids"},
    {"host_nfs", "xprt_bklog_u"},
    {"host_nfs", "xprt_req_u"},
    {"host_nfs", "read_timeouts"},
    {"host_nfs", "write_timeouts"},

    {"host_proc", "uid"},
    {"host_proc", "vm_lck"},
    {"host_proc", "vm_pte"},
    {"host_proc", "vm_hwm"},

    {"host_ps", "load_1"},
    {"host_ps", "load_5"},
    {"host_ps", "load_15"},

    {"host_numa", "local_node"},
    {"host_numa", "interleave_hit"},

    {"host_tmpfs", "bytes_avail"},
    {"host_tmpfs", "bytes_used"},
    {"host_tmpfs", "files_used"},

    {"host_vfs", "dentry_use"},
    {"host_vfs", "file_use"},
    {"host_vfs", "inode_use"},

    {"host_vm", "nr_anon_transparent_hugepages"},
    {"host_vm", "pgpgin"},
    {"host_vm", "pgpgout"},
    {"host_vm", "pswpin"},
    {"host_vm", "pswpout"},
    {"host_vm", "pgalloc_normal"},
    {"host_vm", "pgfree"},
    {"host_vm", "pgactivate"},
    {"host_vm", "pgdeactivate"},
    {"host_vm", "pgfault"},
    {"host_vm", "pgmajfault"},
    {"host_vm", "pgrefill_normal"},
    {"host_vm", "pgsteal_normal"},
    {"host_vm", "pgscan_kswapd_normal"},
    {"host_vm", "pgscan_direct_normal"},
    {"host_vm", "pginodesteal"},
    {"host_vm", "slabs_scanned"},
    {"host_vm", "kswapd_steal"},
    {"host_vm", "kswapd_inodesteal"},
    {"host_vm", "pageoutrun"},
    {"host_vm", "allocstall"},
    {"host_vm", "pgrotated"},
    {"host_vm", "thp_fault_alloc"},
    {"host_vm", "thp_fault_fallback"},
    {"host_vm", "thp_collapse_alloc"},
    {"host_vm", "thp_collapse_alloc_failed"},
    {"host_vm", "thp_split"},

    {"host_cpu_hw", "cpu_util_total_accum_us"},
    {"host_cpu_hw", "cpu_util_user_accum_us"},
    {"host_cpu_hw", "cpu_util_sys_accum_us"},
    {"host_cpu_hw", "cpu_util_irq_accum_us"},
    {"host_cpu_hw", "cpu_util_nice_accum_us"},
    {"host_cpu_hw", "dcgm_cpu_power_util_w"},
    {"host_cpu_hw", "dcgm_cpu_power_limit_w"},

    {"nvidia_gpu", "gpu_count"},
    {"amd_gpu", "gpu_count"},
    {"intel_gpu", "gpu_count"},
};

void collect_tier_set_enabled(int enabled)
{
  g_slow_tier_enabled = enabled ? 1 : 0;
}

int collect_tier_enabled(void)
{
  return g_slow_tier_enabled;
}

void collect_tier_set_phase(enum collect_phase phase)
{
  g_collect_phase = phase;
}

enum collect_phase collect_tier_get_phase(void)
{
  return g_collect_phase;
}

enum collect_phase collect_tier_effective_phase(int write_hdr)
{
  return write_hdr ? COLLECT_FULL : g_collect_phase;
}

static int collect_tier_key_has_error_suffix(const char *key)
{
  size_t len;

  if (key == NULL)
    return 0;
  len = strlen(key);
  if (len >= 6 && strcmp(key + len - 6, "_error") == 0)
    return 1;
  if (len >= 7 && strcmp(key + len - 7, "_errors") == 0)
    return 1;
  return 0;
}

int collect_tier_key_is_slow(const char *type_name, const char *key)
{
  size_t i;

  if (type_name == NULL || key == NULL)
    return 0;

  if (collect_tier_key_has_error_suffix(key))
    return 1;

  for (i = 0; i < sizeof(collect_tier_slow_table) / sizeof(collect_tier_slow_table[0]); i++) {
    if (strcmp(type_name, collect_tier_slow_table[i].type) == 0 &&
        strcmp(key, collect_tier_slow_table[i].key) == 0)
      return 1;
  }
  return 0;
}

void collect_tier_apply_to_type(struct stats_type *type)
{
  size_t j;

  if (type == NULL || !g_slow_tier_enabled)
    return;

  for (j = 0; j < type->st_schema.sc_len; j++) {
    struct schema_entry *se = type->st_schema.sc_ent[j];

    if (se == NULL)
      continue;
    if (collect_tier_key_is_slow(type->st_name, se->se_key))
      se->se_collect_tier = COLLECT_TIER_SLOW;
  }
}

int collect_tier_key_active(const struct stats_type *type, int idx)
{
  const struct schema_entry *se;

  if (!g_slow_tier_enabled || g_collect_phase == COLLECT_FULL)
    return 1;
  if (type == NULL || idx < 0 || (size_t)idx >= type->st_schema.sc_len)
    return 1;
  se = type->st_schema.sc_ent[idx];
  if (se == NULL)
    return 1;
  return se->se_collect_tier == COLLECT_TIER_FAST;
}
