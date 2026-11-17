/* stats_type_prune_untouched: a type that opts in (host_proc) loses the rows a collect cycle did
 * not touch, so an exited PID stops being emitted; a real-device type keeps its untouched rows,
 * which emit NOT_UPDATED. */
#include <assert.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "collect_tier.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel.h"

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;
extern struct stats_type proc_stats_type;

static struct stats_type g_proc_like;
static struct stats_type g_device;

static void init_type(struct stats_type *type, const char *def, int prune)
{
  memset(type, 0, sizeof(*type));
  snprintf(type->st_name, sizeof(type->st_name), "%s", prune ? "host_tt_proc" : "host_tt_dev");
  type->st_schema_def = (char *)def;
  assert(stats_type_init(type) == 0);
  collect_tier_apply_to_type(type);
  type->st_prune_untouched = prune;
}

static int has_row(struct stats_type *type, const char *dev)
{
  return dict_ref(&type->st_current_dict, dev) != NULL;
}

static void test_prune_drops_only_untouched_rows(void)
{
  struct stats *kept;
  struct stats *back;

  init_type(&g_proc_like, "a,E b,E", 1);
  kept = get_current_stats(&g_proc_like, "alive/1");
  assert(get_current_stats(&g_proc_like, "exited/2") != NULL);
  stats_type_mark_not_updated(&g_proc_like);
  stats_set(kept, "a", 5ULL);

  stats_type_prune_untouched(&g_proc_like);
  assert(has_row(&g_proc_like, "alive/1"));
  assert(!has_row(&g_proc_like, "exited/2"));
  assert(g_proc_like.st_current_dict.d_count == 1);

  /* The entity returns: a fresh row, not the old values. */
  back = get_current_stats(&g_proc_like, "exited/2");
  assert(back != NULL && back->s_val_state[0] == STATS_SENTINEL_NOT_UPDATED);
  stats_type_destroy(&g_proc_like);
}

/* A row whose keys carry a real code (cooldown BACKOFF) was touched and stays. */
static void test_prune_keeps_rows_marked_with_a_code(void)
{
  init_type(&g_proc_like, "a,E b,E", 1);
  assert(get_current_stats(&g_proc_like, "p/3") != NULL);
  stats_type_mark_not_updated(&g_proc_like);
  stats_type_set_missing_all(&g_proc_like, STATS_SENTINEL_BACKOFF);
  stats_type_prune_untouched(&g_proc_like);
  assert(has_row(&g_proc_like, "p/3"));
  stats_type_destroy(&g_proc_like);
}

/* A phase with no active key resets nothing, so nothing can be judged untouched. */
static void test_prune_skips_phase_without_active_keys(void)
{
  init_type(&g_proc_like, "a,E,R=S", 1);
  collect_tier_set_enabled(1);
  collect_tier_set_phase(COLLECT_FAST_ONLY);
  assert(get_current_stats(&g_proc_like, "p/4") != NULL);
  stats_type_mark_not_updated(&g_proc_like);
  stats_type_prune_untouched(&g_proc_like);
  assert(has_row(&g_proc_like, "p/4"));

  collect_tier_set_phase(COLLECT_FULL);
  stats_type_mark_not_updated(&g_proc_like);
  stats_type_prune_untouched(&g_proc_like);
  assert(!has_row(&g_proc_like, "p/4"));
  collect_tier_set_enabled(0);
  stats_type_destroy(&g_proc_like);
}

static void test_real_device_row_is_never_pruned(void)
{
  struct stats *port;

  init_type(&g_device, "a,E b,E", 0);
  port = get_current_stats(&g_device, "mlx5_0.1");
  stats_set(port, "a", 7ULL);
  stats_type_mark_not_updated(&g_device);
  stats_type_prune_untouched(&g_device);
  assert(has_row(&g_device, "mlx5_0.1"));
  assert(port->s_val_state[0] == STATS_SENTINEL_NOT_UPDATED);
  assert(port->s_val_state[1] == STATS_SENTINEL_NOT_UPDATED);
  stats_type_destroy(&g_device);
}

static int proc_row_for_pid(pid_t pid)
{
  char needle[32];
  size_t j = 0;
  char *dev;

  snprintf(needle, sizeof(needle), "/%ld/", (long)pid);
  while ((dev = dict_for_each(&proc_stats_type.st_current_dict, &j)) != NULL)
    if (strstr(dev, needle) != NULL)
      return 1;
  return 0;
}

static void collect_proc_cycle(void)
{
  stats_type_mark_not_updated(&proc_stats_type);
  proc_stats_type.st_collect(&proc_stats_type);
  stats_type_prune_untouched(&proc_stats_type);
}

/* End to end on the real /proc: the child's row exists while it runs and is gone after it exits.
 * host_proc skips uid 0 processes, so this part only runs as a non-root user. */
static void test_proc_collect_prunes_exited_child(void)
{
  pid_t child;
  int found;

  assert(proc_stats_type.st_prune_untouched == 1);
  if (geteuid() == 0) {
    printf("test_proc_prune_vanished: root, skipping the live /proc child check\n");
    return;
  }
  assert(stats_type_init(&proc_stats_type) == 0);
  child = fork();
  assert(child >= 0);
  if (child == 0) {
    pause();
    _exit(0);
  }
  collect_proc_cycle();
  found = proc_row_for_pid(child);
  if (!found && proc_stats_type.st_current_dict.d_count == 0) {
    /* qemu-user: the emulated scan of the host /proc yields no process at all. */
    printf("test_proc_prune_vanished: host_proc saw no process (emulated /proc), skipping the "
           "live /proc child check\n");
    kill(child, SIGKILL);
    waitpid(child, NULL, 0);
    stats_type_destroy(&proc_stats_type);
    return;
  }
  /* Reap the child before asserting so a failure leaves no orphan. */
  assert(kill(child, SIGKILL) == 0);
  assert(waitpid(child, NULL, 0) == child);
  assert(found);
  collect_proc_cycle();
  assert(!proc_row_for_pid(child));
  stats_type_destroy(&proc_stats_type);
}

int main(void)
{
  test_prune_drops_only_untouched_rows();
  test_prune_keeps_rows_marked_with_a_code();
  test_prune_skips_phase_without_active_keys();
  test_real_device_row_is_never_pruned();
  test_proc_collect_prunes_exited_child();
  printf("test_proc_prune_vanished passed\n");
  return 0;
}
