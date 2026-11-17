/* host_nfs: NFS client mountstats from /proc/self/mountstats. */
#ifndef HOST_NFS_H_
#define HOST_NFS_H_

/* Test seam: read mountstats from `path` instead of /proc/self/mountstats (NULL or "" restores
 * it). */
void nfs_test_set_mountstats_path(const char *path);

#endif
