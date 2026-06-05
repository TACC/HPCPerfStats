/* Read /proc and /sys scalars and feed parsed values into stats_set. */
#ifndef _COLLECT_H_
#define _COLLECT_H_

#if GCC_VERSION >= 3005
#define __ATTRIBUTE__SENTINEL __attribute__((sentinel))
#else
#define __ATTRIBUTE__SENTINEL
#endif

struct stats;

/* Read small /proc and /sys scalars via open/read (not stdio) to cut syscall overhead. */
int path_collect_single(const char *path, unsigned long long *dest);
int path_collect_list(const char *path, ...) __ATTRIBUTE__SENTINEL;
int path_collect_key_list(const char *path, struct stats *stats, ...)
  __ATTRIBUTE__SENTINEL;
int path_collect_key_value(const char *path, struct stats *stats);
int path_collect_key_value_dir(const char *dir_path, struct stats *stats);

int str_collect_key_list(const char *str, struct stats *stats, ...)
  __ATTRIBUTE__SENTINEL;
int str_collect_prefix_key_list(const char *str, struct stats *stats,
                                const char *prefix, ...)
  __ATTRIBUTE__SENTINEL;

/* Invalidate per-process collect caches (SIGHUP, jobid/rotate reset, shutdown). */
void cpu_stats_invalidate_file_caches(void);
void net_stats_invalidate_iface_cache(void);

#endif
