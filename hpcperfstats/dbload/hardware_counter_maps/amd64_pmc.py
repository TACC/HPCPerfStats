"""AMD64 PMC event encoding and schema; core/socket stats extraction from amd64_pmc for a host/job.

AI generated.
"""
import numpy

# amd64
# define PERF_EVENT(event_select, unit_mask) \
#  ( (event_select & 0xFF) \
#  | (unit_mask << 8) \
#  | (1UL << 16) /* Count in user mode (CPL == 0). */ \
#  | (1UL << 17) /* Count in OS mode (CPL > 0). */ \
#  | (1UL << 22) /* Enable. */ \
#  | ((event_select & 0xF00) << 24) \
#  )


def perf_event(event_select, unit_mask):
  """Encode AMD64 PERF_EVENT from event_select and unit_mask (user+os mode, enable, high bits).

    AI generated.
    """
  return (event_select & 0xFF) | (unit_mask << 8) | (1 << 16) | (1 << 17) | (
      1 << 22) | ((event_select & 0xF00) << 24)


#define DRAMaccesses   PERF_EVENT(0xE0, 0x07) /* DCT0 only */
#define HTlink0Use     PERF_EVENT(0xF6, 0x37) /* Counts all except NOPs */
#define HTlink1Use     PERF_EVENT(0xF7, 0x37) /* Counts all except NOPs */
#define HTlink2Use     PERF_EVENT(0xF8, 0x37) /* Counts all except NOPs */
#define UserCycles    (PERF_EVENT(0x76, 0x00) & ~(1UL << 17))
#define DCacheSysFills PERF_EVENT(0x42, 0x01) /* Counts DCache fills from beyond the L2 cache. */
#define SSEFLOPS       PERF_EVENT(0x03, 0x7F) /* Counts single & double, add, multiply, divide & sqrt FLOPs. */

dram_accesses = perf_event(0xE0, 0x07)
ht_link_0_use = perf_event(0xF6, 0x37)
ht_link_1_use = perf_event(0xF7, 0x37)
ht_link_2_use = perf_event(0xF8, 0x37)
user_cycles = perf_event(0x76, 0x00) & ~(1 << 17)
dcache_sys_fills = perf_event(0x42, 0x01)
sse_flops = perf_event(0x03, 0x7F)

pmc_schema_desc = 'CTL0,C CTL1,C CTL2,C CTL3,C CTR0,E,W=48 CTR1,E,W=48 CTR2,E,W=48 CTR3,E,W=48\n'
core_schema_desc = 'USER,E DCSF,E,U=B SSE_FLOPS,E'  # mult is handled below.
sock_schema_desc = 'DRAM,E,U=B HT0,E,U=B HT1,E,U=B HT2,E,U=B'

ctr_info = {
    #                 (is_core, col, mult)
    user_cycles: (True, 0, 1),
    dcache_sys_fills: (True, 1, 64),  # DCSFs are 64B.
    sse_flops: (True, 2, 1),
    dram_accesses: (False, 0, 64),  # DRAM accesses are 64B.
    ht_link_0_use:
        (False, 1, 4),  # Each HT event counter increment represents 4B.
    ht_link_1_use: (False, 2, 4),
    ht_link_2_use: (False, 3, 4),
}
nr_core_ctrs = 3
nr_sock_ctrs = 4

ctl_values = [[dram_accesses, user_cycles, dcache_sys_fills, sse_flops],
              [user_cycles, ht_link_0_use, dcache_sys_fills, sse_flops],
              [user_cycles, dcache_sys_fills, ht_link_1_use, sse_flops],
              [user_cycles, dcache_sys_fills, sse_flops, ht_link_2_use]]

nr_cores = 16
nr_socks = 4


def core_to_sock(c):
  """Map core index to socket index.

    AI generated.
    """
  return c / (nr_cores / nr_socks)


def process_host(host, times):
  """Convert host amd64_pmc stats to amd64_core and amd64_sock; replace stats key.

    AI generated.
    """
  pmc_stats = host.stats['amd64_pmc']
  core_stats = dict((str(i), numpy.zeros((len(times), nr_core_ctrs), numpy.uint64)) \
                    for i in range(0, nr_cores))
  sock_stats = dict((str(i), numpy.zeros((len(times), nr_sock_ctrs), numpy.uint64)) \
                    for i in range(0, nr_socks))
  for c_name, pmc_arr in pmc_stats.iteritems():
    if not c_name.isdigit():
      # XXX
      return
    c = int(c_name)
    if not (0 <= c and c < nr_cores):
      # XXX
      return
    if pmc_arr.shape != (len(times), 8):
      # XXX
      return
    core_arr = core_stats[c_name]
    sock_arr = sock_stats[str(core_to_sock(c))]
    for i, v in enumerate(pmc_arr):
      if not all(v[0:4] == ctl_values[c % len(ctl_values)]):
        # error("reprogrammed control values: %d %d %d %d\n", v[0], v[1], v[2], v[3])
        return
      for j in range(0, 4):
        is_core, col, mult = ctr_info[v[j]]
        if is_core:
          core_arr[i, col] = mult * v[j + 4]
        else:
          sock_arr[i, col] = mult * v[j + 4]
  host.stats['amd64_core'] = core_stats
  host.stats['amd64_sock'] = sock_stats
  del host.stats['amd64_pmc']


def process_job(job):
  """If job has amd64_pmc schema, add amd64_core/amd64_sock schemas and process each host; remove amd64_pmc.

    AI generated.
    """
  pmc_schema = job.schemas.get('amd64_pmc')
  if not pmc_schema:
    return
  if pmc_schema.desc != pmc_schema_desc:
    # XXX
    return

  core_schema = job.get_schema('amd64_core', core_schema_desc)
  sock_schema = job.get_schema('amd64_sock', sock_schema_desc)
  for host in job.hosts.itervalues():
    if 'amd64_pmc' not in host.stats:
      del job.schemas['amd64_pmc']
      return
    process_host(host, job.times)

  del job.schemas['amd64_pmc']
