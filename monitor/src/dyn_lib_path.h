/* Allowlist absolute paths for privileged dlopen overrides. */
#ifndef DYN_LIB_PATH_H
#define DYN_LIB_PATH_H

/* Return 1 if path is an absolute regular file under a trusted lib prefix
 * with no "/../" components; else 0. */
int dyn_lib_path_allowed(const char *path);

#endif
