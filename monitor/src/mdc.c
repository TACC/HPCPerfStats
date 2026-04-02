#include <stdio.h>
#include <stdlib.h>
#include <dirent.h>
#include "stats.h"
#include "fileio.h"
#include "trace.h"
#include "string1.h"
#include "lustre_obd_to_mnt.h"
#include "path_open_fail_once.h"

#define MDC_DIR_PATH "/proc/fs/lustre/mdc"

#define KEYS \
  X(ldlm_cancel, "E", ""), \
  X(mds_close, "E", ""), \
  X(mds_getattr, "E", ""), \
  X(mds_getattr_lock, "E", ""), \
  X(mds_getxattr, "E", ""), \
  X(mds_readpage, "E", ""), \
  X(mds_statfs, "E", ""), \
  X(mds_sync, "E", ""), \
  X(reqs, "E", ""), \
  X(wait, "E,U=us", "")

static void mdc_collect_fs(struct stats *stats, const char *d_name)
{
  char *path = NULL;
  FILE *file = NULL;
  char file_buf[4096];
  char *line_buf = NULL;
  size_t line_buf_size = 0;

  if (asprintf(&path, "%s/%s/stats", MDC_DIR_PATH, d_name) < 0) {
    ERROR("cannot create path: %m\n");
    goto out;
  }

  file = path_file_fopen_read(path);
  if (file == NULL)
    goto out;
  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));

  // $ cat /proc/fs/lustre/mdc/work-MDT0000-mdc-ffff8104435c8c00/stats
  // snapshot_time             1315505833.280916 secs.usecs
  // req_waittime              1885503 samples [usec] 32 4826358 908745020 670751020176306
  // req_active                1885503 samples [reqs] 1 357 2176198 17811836
  // mds_getattr               231 samples [usec] 50 5735 45029 60616599
  // mds_close                 312481 samples [usec] 38 356200 47378914 142563767708
  // mds_readpage              12187 samples [usec] 80 16719 3185676 4923626688
  // mds_connect               1 samples [usec] 302 302 302 91204
  // mds_getstatus             1 samples [usec] 273 273 273 74529
  // mds_statfs                30 samples [usec] 130 444 7765 2137505
  // mds_sync                  262 samples [usec] 3042 4826358 33767303 73003166559545
  // mds_quotactl              6030 samples [usec] 466 1667578 23448377 4610731893889
  // ldlm_cancel               169832 samples [usec] 32 8257 25752413 4873947759
  // obd_ping                  112820 samples [usec] 40 1543848 548167082 592878039802964

  while (getline(&line_buf, &line_buf_size, file) >= 0) {
    char *line = line_buf;
    char *key = wsep(&line);
    if (key == NULL || line == NULL)
      continue;

    unsigned long long count = 0, sum = 0;
    if (sscanf(line, "%llu samples %*s %*u %*u %llu", &count, &sum) != 2)
      continue;

    if (strcmp(key, "req_waittime") == 0) {
      stats_set(stats, "reqs", count);
      stats_set(stats, "wait", sum);
    } else {
      stats_set(stats, key, count);
    }
  }

 out:
  free(line_buf);
  if (file != NULL)
    fclose(file);
  free(path);
}

static void mdc_collect(struct stats_type *type)
{
  const char *mdc_dir_path = MDC_DIR_PATH;
  DIR *mdc_dir = NULL;

  mdc_dir = path_opendir_or_record_fail(mdc_dir_path);
  if (mdc_dir == NULL)
    goto out;

  struct dirent *de;
  while ((de = readdir(mdc_dir)) != NULL) {
    struct stats *stats = NULL;
    const char *mnt;

    if (de->d_type != DT_DIR || *de->d_name == '.')
      continue;

    mnt = lustre_obd_to_mnt(de->d_name);
    if (mnt == NULL)
      continue;

    TRACE("d_name `%s', mnt `%s'\n", de->d_name, mnt);

    stats = get_current_stats(type, mnt);
    if (stats == NULL)
      continue;

    mdc_collect_fs(stats, de->d_name);
  }

 out:
  if (mdc_dir != NULL)
    closedir(mdc_dir);
}

struct stats_type mdc_stats_type = {
  .st_name = "mdc",
  .st_collect = &mdc_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
