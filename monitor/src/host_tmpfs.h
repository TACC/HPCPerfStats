/* host_tmpfs: /tmp usage from statfs when /tmp is a tmpfs mount. */
#ifndef HOST_TMPFS_H_
#define HOST_TMPFS_H_

/* Test seam: read mounts from `path` instead of /proc/mounts (NULL or "" restores it). */
void tmpfs_test_set_mounts_path(const char *path);

#endif
