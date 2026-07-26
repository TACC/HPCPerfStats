#ifndef BEEGFS_CTL_PARSE_H_
#define BEEGFS_CTL_PARSE_H_

#include <stddef.h>

/* Pure helpers for BeeGFS clientstats text and mount/option parsing. */

#define BEEGFS_CTL_MIB_TO_BYTES 1048576ULL

struct beegfs_ctl_counters {
  unsigned long long vfs_read_bytes;
  unsigned long long vfs_write_bytes;
  unsigned long long vfs_read_ops;
  unsigned long long vfs_write_ops;
  unsigned long long vfs_open_ops;
  unsigned long long vfs_close_ops;
  unsigned long long vfs_getattr_ops;
  unsigned long long vfs_setattr_ops;
  unsigned long long vfs_truncate_ops;
  unsigned long long vfs_readdir_ops;
  unsigned long long vfs_create_ops;
  unsigned long long vfs_mkdir_ops;
  unsigned long long vfs_rmdir_ops;
  unsigned long long vfs_rename_ops;
  unsigned long long vfs_unlink_ops;
  unsigned long long vfs_link_ops;
  unsigned long long vfs_statfs_ops;
  unsigned have_vfs_read_bytes : 1;
  unsigned have_vfs_write_bytes : 1;
  unsigned have_vfs_read_ops : 1;
  unsigned have_vfs_write_ops : 1;
  unsigned have_vfs_open_ops : 1;
  unsigned have_vfs_close_ops : 1;
  unsigned have_vfs_getattr_ops : 1;
  unsigned have_vfs_setattr_ops : 1;
  unsigned have_vfs_truncate_ops : 1;
  unsigned have_vfs_readdir_ops : 1;
  unsigned have_vfs_create_ops : 1;
  unsigned have_vfs_mkdir_ops : 1;
  unsigned have_vfs_rmdir_ops : 1;
  unsigned have_vfs_rename_ops : 1;
  unsigned have_vfs_unlink_ops : 1;
  unsigned have_vfs_link_ops : 1;
  unsigned have_vfs_statfs_ops : 1;
};

/*! 1 if fstype is a BeeGFS client (beegfs / beegfs_nodev). */
int beegfs_fstype_is_beegfs(const char *fstype);

/*! Extract cfgFile= path from mount options into out (NUL-terminated). 0 ok, -1 missing. */
int beegfs_cfgfile_from_mnt_opts(const char *opts, char *out, size_t out_sz);

/*! 1 if line is the cluster aggregate (Sum:), never use as per-host counters. */
int beegfs_ctl_line_is_sum(const char *line);

/*! 1 if first field of line matches any local identity (IP / hostname). */
int beegfs_ctl_line_matches_local(const char *line, const char *const *idents, size_t n_idents);

/*! Parse one clientstats data line (node id already known to be local). Returns 0 on success. */
int beegfs_ctl_parse_stats_line(const char *line, struct beegfs_ctl_counters *out);

/*! Scan multi-line ctl output; fill out from the first matching local line. Returns 1 if found. */
int beegfs_ctl_select_local_line(const char *text, const char *const *idents, size_t n_idents,
                                 struct beegfs_ctl_counters *out);

#endif
