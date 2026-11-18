#ifndef BEEGFS_MOUNT_H_
#define BEEGFS_MOUNT_H_

#include <stddef.h>

/*! 1 if fstype is a BeeGFS client (beegfs / beegfs_nodev). */
int beegfs_fstype_is_beegfs(const char *fstype);

/*! Extract cfgFile= path from mount options into out (NUL-terminated). 0 ok, -1 missing. */
int beegfs_cfgfile_from_mnt_opts(const char *opts, char *out, size_t out_sz);

#endif
