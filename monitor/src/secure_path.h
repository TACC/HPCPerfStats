/* Private directory create/validate for daemon dump and DEBUG shm paths. */
#ifndef SECURE_PATH_H
#define SECURE_PATH_H

#include <sys/types.h>

/* mkdir(path, mode) if missing; on EEXIST require real directory owned by euid
 * with no group/other write. Returns 0 on success, -1 on failure. */
int ensure_private_dir(const char *path, mode_t mode);

#endif
