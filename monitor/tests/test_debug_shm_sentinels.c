/* DEBUG sentinel provenance (built with -DDEBUG in every build): each stats_set_missing* call
 * site registers in the site registry and records its key's site / errno / run length; the
 * rendered "sentinels" lines, the /dev/shm files, and the debug_fault_inject table. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "collect_tier.h"
#include "fileio.h"
#include "schema.h"
#include "stats.h"
#include "stats_buffer_debug_shm.h"
#include "stats_registry.h"
#include "stats_sentinel_debug.h"

static struct stats_type g_type;
struct stats_type *const stats_type_table[] = {&g_type};
const size_t stats_type_nr = 1;

static int g_line_b;

static void mark_b_read_failed(struct stats *s)
{
  errno = ENOENT;
  g_line_b = __LINE__ + 1;
  stats_set_missing(s, "b", STATS_SENTINEL_READ_FAILED);
}

/* A registered site that never runs: the coverage report must count it. */
static void never_called_site(struct stats *s)
{
  stats_set_missing(s, "a", STATS_SENTINEL_BACKOFF);
}

static char *render(void)
{
  size_t len = 0;
  char *buf = stats_sentinel_debug_render(&len);

  assert(buf != NULL && strlen(buf) == len);
  return buf;
}

static void cycle(struct stats *s, time_t now, int set_b)
{
  stats_type_mark_not_updated(&g_type);
  stats_set(s, "a", (unsigned long long)now);
  if (set_b)
    stats_set(s, "b", 7);
  else
    mark_b_read_failed(s);
  stats_sentinel_debug_cycle_end(&g_type, now);
}

static void test_provenance_lines_and_run_lengths(struct stats *s)
{
  char want[256];
  char *buf;

  cycle(s, 100, 0);
  buf = render();
  snprintf(want, sizeof(want),
           "host_tt d0 b 3 READ_FAILED %d test_debug_shm_sentinels.c:%d 100 100 1\n", ENOENT,
           g_line_b);
  assert(strstr(buf, want) != NULL);
  assert(strstr(buf, "host_tt d0 c 1 NOT_UPDATED 0 - 100 100 1\n") != NULL);
  assert(strstr(buf, "host_tt d0 a ") == NULL);
  free(buf);

  cycle(s, 130, 0);
  buf = render();
  snprintf(want, sizeof(want),
           "host_tt d0 b 3 READ_FAILED %d test_debug_shm_sentinels.c:%d 100 130 2\n", ENOENT,
           g_line_b);
  assert(strstr(buf, want) != NULL);
  free(buf);

  /* b recovers (its line goes); c stays NOT_UPDATED with no site, the run keeps counting. */
  cycle(s, 160, 1);
  buf = render();
  assert(strstr(buf, "host_tt d0 b ") == NULL);
  assert(strstr(buf, "host_tt d0 c 1 NOT_UPDATED 0 - 100 160 3\n") != NULL);
  free(buf);
}

/* The _all forms record a site only on keys they actually mark (still NOT_UPDATED). */
static void test_all_forms_note_only_unset_keys(struct stats *s)
{
  int line_all;
  char want[256];
  char *buf;

  stats_type_mark_not_updated(&g_type);
  stats_set(s, "a", 1);
  errno = EACCES;
  line_all = __LINE__ + 1;
  stats_set_missing_all(s, STATS_SENTINEL_PERMISSION_DENIED);
  stats_sentinel_debug_cycle_end(&g_type, 200);
  buf = render();
  assert(strstr(buf, "host_tt d0 a ") == NULL);
  snprintf(want, sizeof(want),
           "host_tt d0 b 4 PERMISSION_DENIED %d test_debug_shm_sentinels.c:%d 200 200 1\n", EACCES,
           line_all);
  assert(strstr(buf, want) != NULL);
  free(buf);

  stats_type_mark_not_updated(&g_type);
  stats_type_set_missing_all(&g_type, STATS_SENTINEL_BACKOFF);
  stats_sentinel_debug_cycle_end(&g_type, 230);
  buf = render();
  assert(strstr(buf, "host_tt d0 a 9 BACKOFF") != NULL);
  assert(strstr(buf, "host_tt d0 c 9 BACKOFF") != NULL);
  free(buf);
}

static void test_site_registry_counts_hits(void)
{
  char *text = NULL;
  size_t len = 0;
  char want[96];
  FILE *mem = open_memstream(&text, &len);
  size_t never;

  assert(mem != NULL);
  never = stats_sentinel_sites_report(mem);
  assert(fclose(mem) == 0);
  snprintf(want, sizeof(want), "test_debug_shm_sentinels.c:%d 2\n", g_line_b);
  assert(strstr(text, want) != NULL);
  assert(never == 1); /* never_called_site */
  free(text);
}

static void test_fault_inject_table(void)
{
  int err = 0;

  assert(stats_fault_inject_set("open:/proc/stat!13@2,opendir") == 2);
  assert(STATS_FAULT("open", "/proc/stat", &err) && err == 13);
  assert(!STATS_FAULT("open", "/proc/meminfo", &err));
  assert(STATS_FAULT("opendir", "/sys/anything", &err) && err == EIO);
  errno = 0;
  assert(file_fopen_read("/proc/stat") == NULL && errno == 13);
  assert(stats_fault_inject_fired(0) == 2);
  stats_fault_inject_tick();
  assert(STATS_FAULT("open", "/proc/stat", &err));
  stats_fault_inject_tick();
  assert(!STATS_FAULT("open", "/proc/stat", &err)); /* released after 2 cycles */
  assert(STATS_FAULT("opendir", "/x", &err));       /* no @ticks: until cleared */

  assert(stats_fault_inject_set("open@0") == -1);
  assert(!STATS_FAULT("opendir", "/x", &err));
  assert(stats_fault_inject_set("open!x") == -1);
  assert(stats_fault_inject_set(":/proc") == -1);
  assert(stats_fault_inject_set("") == 0);
  assert(stats_fault_inject_set(NULL) == 0);
  assert(stats_fault_inject_fired(5) == 0);
}

/* fault_fired lists each entry as configured with its fire count; 0 marks a point never reached,
 * which the live checker reports as a NOTE. */
static void test_fault_inject_report_counts_fires(void)
{
  char *text = NULL;
  size_t len = 0;
  FILE *mem;
  int err = 0;

  assert(stats_fault_inject_set("open:/proc/stat!13@1+2,dcgm_fetch@1+3") == 2);
  assert(!STATS_FAULT("open", "/proc/stat", &err));
  stats_fault_inject_tick();
  assert(STATS_FAULT("open", "/proc/stat", &err));
  mem = open_memstream(&text, &len);
  assert(mem != NULL);
  assert(stats_fault_inject_report(mem) == 2);
  assert(fclose(mem) == 0);
  assert(strcmp(text, "open:/proc/stat!13@1+2 1\ndcgm_fetch@1+3 0\n") == 0);
  free(text);

  text = NULL;
  assert(stats_fault_inject_set("open@0") == -1); /* a rejected spec leaves no entries */
  mem = open_memstream(&text, &len);
  assert(mem != NULL);
  assert(stats_fault_inject_report(mem) == 0);
  assert(fclose(mem) == 0);
  assert(len == 0);
  free(text);
}

/* Opens dfd/name without following links and asserts mode 0600 on the opened fd. */
static int open_mode_0600(int dfd, const char *name)
{
  struct stat st;
  int fd = openat(dfd, name, O_RDONLY | O_NOFOLLOW);

  assert(fd >= 0 && fstat(fd, &st) == 0 && (st.st_mode & 0777) == 0600);
  return fd;
}

/* Reads dfd/name (mode 0600, no link follow) into buf and removes it. */
static void read_and_unlink_0600(int dfd, const char *name, char *buf, size_t size)
{
  FILE *f = fdopen(open_mode_0600(dfd, name), "r");
  size_t n;

  assert(f != NULL);
  n = fread(buf, 1, size - 1, f);
  buf[n] = '\0';
  fclose(f);
  assert(unlinkat(dfd, name, 0) == 0);
}

static void test_shm_files(void)
{
  char dir[96];
  int dfd;
  char *buf;
  char got[4096];

  snprintf(dir, sizeof(dir), "/dev/shm/hps_test_sentinels_%ld", (long)getpid());
  if (access("/dev/shm", W_OK) != 0) {
    printf("test_debug_shm_sentinels: /dev/shm not writable, skipping the shm file check\n");
    return;
  }
  setenv("HPCPERFSTATS_DEBUG_SHM_DIR", dir, 1);
  stats_buffer_debug_shm_init();
  assert(stats_fault_inject_set("dcgm_fetch@1+3") == 1);
  stats_sentinel_debug_write_shm();

  dfd = open(dir, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
  assert(dfd >= 0);
  read_and_unlink_0600(dfd, "sentinels", got, sizeof(got));
  buf = render();
  assert(strcmp(got, buf) == 0);
  free(buf);
  read_and_unlink_0600(dfd, "sentinel_sites", got, sizeof(got));
  read_and_unlink_0600(dfd, "fault_fired", got, sizeof(got));
  assert(strcmp(got, "dcgm_fetch@1+3 0\n") == 0);

  /* An empty table still writes the file, so a stale run's counts never survive a restart. */
  assert(stats_fault_inject_set(NULL) == 0);
  stats_sentinel_debug_write_shm();
  read_and_unlink_0600(dfd, "fault_fired", got, sizeof(got));
  assert(got[0] == '\0');
  assert(unlinkat(dfd, "sentinels", 0) == 0);
  assert(unlinkat(dfd, "sentinel_sites", 0) == 0);
  close(dfd);
  assert(rmdir(dir) == 0);
}

int main(int argc, char **argv)
{
  struct stats *s;

  (void)argv;
  memset(&g_type, 0, sizeof(g_type));
  snprintf(g_type.st_name, sizeof(g_type.st_name), "%s", "host_tt");
  g_type.st_schema_def = (char *)"a,E b,E c,E";
  assert(stats_type_init(&g_type) == 0);
  collect_tier_apply_to_type(&g_type);
  g_type.st_enabled = 1;
  s = get_current_stats(&g_type, "d0");
  assert(s != NULL && s->s_val_prov != NULL);
  if (argc > 99)
    never_called_site(s);

  test_provenance_lines_and_run_lengths(s);
  test_all_forms_note_only_unset_keys(s);
  test_site_registry_counts_hits();
  test_fault_inject_table();
  test_fault_inject_report_counts_fires();
  test_shm_files();
  stats_type_destroy(&g_type);
  printf("test_debug_shm_sentinels passed\n");
  return 0;
}
