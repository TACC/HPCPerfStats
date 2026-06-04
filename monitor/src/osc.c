#include <stdio.h>
#include <stdlib.h>
#include <dirent.h>
#include "stats.h"
#include "fileio.h"
#include "trace.h"
#include "string1.h"
#include "lustre_obd_to_mnt.h"
#include "path_open_fail_once.h"

#define OSC_DIR_PATH "/proc/fs/lustre/osc"

#define KEYS \
  X(read_bytes, "E,U=B", ""), \
  X(write_bytes, "E,U=B", ""), \
  X(ost_destroy, "E", ""), \
  X(ost_punch, "E", ""), \
  X(ost_read, "E", ""), \
  X(ost_setattr, "E", ""), \
  X(ost_statfs, "E", ""), \
  X(ost_write, "E", ""), \
  X(reqs, "E", ""), \
  X(wait, "E,U=us", "")

static void osc_collect_fs(struct stats *stats, const char *d_name)
{
  char *path = NULL;
  FILE *file = NULL;
  char file_buf[4096];
  char *line_buf = NULL;
  size_t line_buf_size = 0;

  if (asprintf(&path, "%s/%s/stats", OSC_DIR_PATH, d_name) < 0) {
    ERROR("cannot create path: %m\n");
    goto out;
  }

  file = path_file_fopen_read(path);
  if (file == NULL)
    goto out;
  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));

  // $ cat /proc/fs/lustre/osc/work-OST0000-osc-ffff81032792f800/stats
  // snapshot_time             1315506558.236162 secs.usecs
  // req_waittime              2193784 samples [usec] 31 19323671 22792548119 2825876578353643
  // req_active                2193784 samples [reqs] 1 19 4197043 17147705
  // read_bytes                591036 samples [bytes] 4096 1048576 452705124352 458909478532677632
  // write_bytes               298332 samples [bytes] 1 1048576 249338619646 258272714735050642
  // ost_setattr               29934 samples [usec] 53 821747 66185440 8729086271842
  // ost_read                  591567 samples [usec] 58 2298702 10257217364 690745503006198
  // ost_write                 298332 samples [usec] 327 1999739 6699685897 755869697207251
  // ost_destroy               112511 samples [usec] 86 19323671 1538924188 1014647386873938
  // ost_connect               1 samples [usec] 13811 13811 13811 190743721
  // ost_punch                 5224 samples [usec] 111 631412 35035618 3480932441880
  // ost_statfs                156 samples [usec] 125 1071 42072 13441240
  // ldlm_cancel               266738 samples [usec] 31 215570 57640045 563159939987
  // obd_ping                  114215 samples [usec] 43 63021 130531843 219411737307

  while (getline(&line_buf, &line_buf_size, file) >= 0) {
    char *line = line_buf;
    char *key = wsep(&line);
    if (key == NULL || line == NULL)
      continue;

    unsigned long long count = 0, sum = 0;
    if (sscanf(line, "%llu samples %*s %*u %*u %llu", &count, &sum) != 2)
      continue;

    if (strcmp(key, "req_waittime") == 0) {
      stats_inc(stats, "reqs", count);
      stats_inc(stats, "wait", sum);
    } else if (strcmp(key, "read_bytes") == 0 || strcmp(key, "write_bytes") == 0) {
      stats_inc(stats, key, sum);
    } else {
      stats_inc(stats, key, count);
    }
  }

 out:
  free(line_buf);
  if (file != NULL)
    fclose(file);
  free(path);
}

static void osc_collect(struct stats_type *type)
{
  const char *osc_dir_path = OSC_DIR_PATH;
  DIR *osc_dir = NULL;

  osc_dir = path_opendir_or_record_fail(osc_dir_path);
  if (osc_dir == NULL)
    goto out;

  struct dirent *de;
  while ((de = readdir(osc_dir)) != NULL) {
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

    osc_collect_fs(stats, de->d_name);
  }

 out:
  if (osc_dir != NULL)
    closedir(osc_dir);
}

struct stats_type osc_stats_type = {
  .st_name = "lustre_osc",
  .st_collect = &osc_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
