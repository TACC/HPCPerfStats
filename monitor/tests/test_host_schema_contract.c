#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "stats.h"

/* host_cpu KEYS from cpu.c */
#define KEYS \
  X(user,    "E,U=cs", "time in user mode"), \
  X(nice,    "E,U=cs", "time in user mode with low priority"), \
  X(system,  "E,U=cs", "time in system mode"), \
  X(idle,    "E,U=cs", "time in idle task"), \
  X(iowait,  "E,U=cs", "time in I/O wait"), \
  X(irq,     "E,U=cs", "time in IRQ"), \
  X(softirq, "E,U=cs", "time in softIRQ")
#define X SCHEMA_DEF
static const char host_cpu_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

/* host_mem KEYS from mem.c */
#define KEYS \
  X(mem_total, "U=KB", ""), \
  X(mem_free, "U=KB", ""), \
  X(mem_used, "U=KB", ""), \
  X(active, "U=KB", ""), \
  X(inactive, "U=KB", ""), \
  X(dirty, "U=KB", ""), \
  X(writeback, "U=KB", ""), \
  X(file_pages, "U=KB", ""), \
  X(mapped, "U=KB", ""), \
  X(anon_pages, "U=KB", ""), \
  X(page_tables, "U=KB", ""), \
  X(nfs_unstable, "U=KB", ""), \
  X(bounce, "U=KB", ""), \
  X(slab, "U=KB", ""), \
  X(anon_huge_pages, "U=KB", ""), \
  X(huge_pages_total, "", ""), \
  X(huge_pages_free, "", "")
#define X SCHEMA_DEF
static const char host_mem_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

/* host_net KEYS from net.c */
#define KEYS \
  X(collisions, "E", ""), \
  X(multicast, "E", ""), \
  X(rx_bytes, "E,U=B", ""), \
  X(rx_compressed, "E", ""), \
  X(rx_crc_errors, "E", ""), \
  X(rx_dropped, "E", ""), \
  X(rx_errors, "E", ""), \
  X(rx_fifo_errors, "E", ""), \
  X(rx_frame_errors, "E", ""), \
  X(rx_length_errors, "E", ""), \
  X(rx_missed_errors, "E", ""), \
  X(rx_over_errors, "E", ""), \
  X(rx_packets, "E", ""), \
  X(tx_aborted_errors, "E", ""), \
  X(tx_bytes, "E,U=B", ""), \
  X(tx_carrier_errors, "E", ""), \
  X(tx_compressed, "E", ""), \
  X(tx_dropped, "E", ""), \
  X(tx_errors, "E", ""), \
  X(tx_fifo_errors, "E", ""), \
  X(tx_heartbeat_errors, "E", ""), \
  X(tx_packets, "E", ""), \
  X(tx_window_errors, "E", "")
#define X SCHEMA_DEF
static const char host_net_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

/* host_ps KEYS from ps.c */
#define KEYS \
  X(ctxt, "E", "context switches"), \
  X(processes, "E", "forks"), \
  X(load_1, "", "1 minute load average (* 100)"), \
  X(load_5, "", "5 minute load average (* 100)"), \
  X(load_15, "", "15 minute load average (* 100)"), \
  X(nr_running, "", ""), \
  X(nr_threads, "", "")
#define X SCHEMA_DEF
static const char host_ps_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

/* host_vm KEYS from vm.c */
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
