/*! \file rapl_likwid_stats.h
 *  Apply LIKWID RAPL socket sampling into monitor stats rows.
 */

#ifndef RAPL_LIKWID_STATS_H
#define RAPL_LIKWID_STATS_H

struct stats_type;

void rapl_likwid_intel_collect_pkg(struct stats_type *type,
				 const char *pkg_key, int cpu_lineno,
				 unsigned pkg_id);

void rapl_likwid_amd_collect_socket_cpu(struct stats_type *type,
					const char *socket_key,
					int cpu_lineno,
					unsigned socket_id,
					int topology_core_id);

#endif
