/* Shared helpers for modern + legacy Lustre proc/sysfs stats files. */
#ifndef LUSTRE_PROC_STATS_H_
#define LUSTRE_PROC_STATS_H_

#include <stdio.h>
#include <stddef.h>

/* Parse "N samples ..." lines. Returns 2 if count+sum, 1 if count only, 0 on fail.
 * Accepts classic multi-field and modern "N samples [reqs]" forms. */
int lustre_parse_samples_count(const char *rest, unsigned long long *count,
                               unsigned long long *sum);

/* Parse "key<sep>value" lines (tabs/spaces); returns 0 on success. */
int lustre_parse_kv_ull(const char *line, const char *want_key, unsigned long long *value);

/* Try opening dir/d_name/<names[i]> in order. On success sets *path_out (caller
 * frees) and *fp_out (caller fclose). Returns 0 on success, -1 if none open. */
int lustre_fopen_obd_named(const char *dir, const char *d_name, const char *const *names,
                           size_t nnames, char **path_out, FILE **fp_out);

#endif
