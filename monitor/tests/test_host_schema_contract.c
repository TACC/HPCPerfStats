#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "stats.h"

#include "host_cpu.h"
#define X SCHEMA_DEF
static const char host_cpu_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

#include "host_mem.h"
#define X SCHEMA_DEF
static const char host_mem_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

#include "host_net.h"
#define X SCHEMA_DEF
static const char host_net_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

#include "host_ps.h"
#define X SCHEMA_DEF
static const char host_ps_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

/* host_vm KEYS from vm.c (no shared header yet) */
#define KEYS \
  X(nr_anon_transparent_hugepages, "", ""), \
  X(pgpgin, "E,U=KB", ""), \
  X(pgpgout, "E,U=KB", ""), \
  X(pswpin, "E", ""), \
  X(pswpout, "E", ""), \
  X(pgalloc_normal, "E", ""), \
  X(pgfree, "E", ""), \
  X(pgactivate, "E", ""), \
  X(pgdeactivate, "E", ""), \
  X(pgfault, "E", ""), \
  X(pgmajfault, "E", ""), \
  X(pgrefill_normal, "E", ""), \
  X(pgsteal_normal, "E", ""), \
  X(pgscan_kswapd_normal, "E", ""), \
  X(pgscan_direct_normal, "E", ""), \
  X(pginodesteal, "E", ""), \
  X(slabs_scanned, "E", ""), \
  X(kswapd_steal, "E", ""), \
  X(kswapd_inodesteal, "E", ""), \
  X(pageoutrun, "E", ""), \
  X(allocstall, "E", ""), \
  X(pgrotated, "E", ""), \
  X(thp_fault_alloc, "E", ""), \
  X(thp_fault_fallback, "E", ""), \
  X(thp_collapse_alloc, "E", ""), \
  X(thp_collapse_alloc_failed, "E", ""), \
  X(thp_split, "E", "")
#define X SCHEMA_DEF
static const char host_vm_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

static void assert_present(const char *schema, const char *frag)
{
  assert(strstr(schema, frag) != NULL);
}

int main(void)
{
  assert_present(host_cpu_schema_def, " user,E,U=cs");
  assert_present(host_cpu_schema_def, " system,E,U=cs");
  assert_present(host_cpu_schema_def, " iowait,E,U=cs");

  assert_present(host_mem_schema_def, " mem_total,U=KB");
  assert_present(host_mem_schema_def, " mem_free,U=KB");
  assert_present(host_mem_schema_def, " anon_pages,U=KB");

  assert_present(host_net_schema_def, " rx_bytes,E,U=B");
  assert_present(host_net_schema_def, " tx_bytes,E,U=B");
  assert_present(host_net_schema_def, " rx_packets,E");

  assert_present(host_ps_schema_def, " load_1,");
  assert_present(host_ps_schema_def, " ctxt,E");
  assert_present(host_ps_schema_def, " nr_running,");

  assert_present(host_vm_schema_def, " pgpgin,E,U=KB");
  assert_present(host_vm_schema_def, " pswpin,E");
  assert_present(host_vm_schema_def, " pgfault,E");

  printf("test_host_schema_contract passed\n");
  return 0;
}
