#ifndef INTEL_TOPOLOGY_WALK_H_
#define INTEL_TOPOLOGY_WALK_H_

#include <stdio.h>

#include "cpuid.h"
#include "stats.h"

typedef void (*intel_topology_pkg_leader_cb)(void *ctx, char *cpu, int pkg_id,
					     int nr_cores);

/*
 * Invoke cb once per package using the first logical cpu with core_id==0 and
 * smt_id==0 (same selection as the legacy SNB/CBO uncore drivers).
 */
static inline void intel_topology_foreach_pkg_leader_core(
    void *ctx, intel_topology_pkg_leader_cb cb)
{
  int i;

  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    int pkg_id = -1;
    int core_id = -1;
    int smt_id = -1;
    int nr_cores = 0;

    snprintf(cpu, sizeof(cpu), "%d", i);
    cpuid_read_cpu_topology(cpu, &pkg_id, &core_id, &smt_id, &nr_cores);
    if (smt_id == 0 && core_id == 0)
      cb(ctx, cpu, pkg_id, nr_cores);
  }
}

#endif
