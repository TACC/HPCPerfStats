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

/* --- 7.3.x clientstats argv (equals-form only) + --names -ib idents --- */

#define BEEGFS_IDENT_LEN 128
#define BEEGFS_CTL_ARGV_MAX 12
#define BEEGFS_CTL_ARGSTR_LEN 512
#define BEEGFS_CTL_DEFAULT_CFGFILE "/etc/beegfs/beegfs-client.conf"

struct beegfs_ctl_argv {
  char *argv[BEEGFS_CTL_ARGV_MAX];
  char nodetype_eq[64];
  char cfgfile_eq[BEEGFS_CTL_ARGSTR_LEN];
  char rwunit_eq[32];
  int argc;
};

/*! 1 if path is absolute and safe for argv (alnum / _ - .). */
int beegfs_path_is_safe(const char *path);

/*! Build equals-form `beegfs-ctl --clientstats` argv (no two-token --nodetype/--mount).
 *  @param nodetype  "storage" or "meta"
 *  @param cfgfile   absolute client.conf path
 *  @param rwunit_b  non-zero → append --rwunit=B
 *  @return argc (>=1) on success, -1 on error; argv is NULL-terminated */
int beegfs_ctl_build_clientstats_argv(struct beegfs_ctl_argv *out, const char *nodetype,
                                      const char *cfgfile, int rwunit_b);

/*! Append `name-ib` aliases for hostname-like idents (not IPv4, not already *-ib).
 *  @return new count (may equal n if full or nothing to add) */
size_t beegfs_idents_add_ib_aliases(char idents[][BEEGFS_IDENT_LEN], size_t n, size_t max_n);

#endif
