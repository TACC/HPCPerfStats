/* fwq.c — Fixed Work Quanta OS-noise probe (after the LLNL/ASC Sequoia FWQ benchmark).
 *
 * One thread per listed CPU, pinned. Each thread repeats a fixed integer work loop and
 * records CLOCK_MONOTONIC_RAW nanoseconds per quantum until the deadline. Any extra time
 * per quantum is a detour taken by something else on that CPU.
 *
 * Usage:
 *   fwq --calibrate US [-c CPUS]   print the work count that takes about US microseconds,
 *                                  measured pinned to the first CPU of CPUS (default: current)
 *   fwq -c CPULIST -s SECONDS -w WORK -o DIR
 *       writes DIR/fwq_cpu<N>.bin (little-endian uint64 ns per quantum)
 *
 * Built at run time by the measurement harness with the host gcc; not shipped in the RPM.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define FWQ_MAX_CPUS 4096

struct fwq_thread {
  pthread_t tid;
  int cpu;
  uint64_t work;
  uint64_t deadline_ns;
  uint64_t *q;
  size_t n;
  size_t cap;
  int err;
};

static pthread_barrier_t g_start;
static volatile uint64_t g_sink;

static uint64_t now_ns(void)
{
  struct timespec ts;

  clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
  return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/* Fixed integer work; the result feeds a volatile sink so the loop is not removed. */
static uint64_t fwq_work(uint64_t work)
{
  uint64_t x = 0x9e3779b97f4a7c15ULL;
  uint64_t i;

  for (i = 0; i < work; i++)
    x = x * 6364136223846793005ULL + 1442695040888963407ULL;
  return x;
}

static int fwq_push(struct fwq_thread *t, uint64_t ns)
{
  if (t->n == t->cap) {
    size_t cap = t->cap ? t->cap * 2 : 65536;
    uint64_t *q = realloc(t->q, cap * sizeof(*q));

    if (q == NULL)
      return -1;
    t->q = q;
    t->cap = cap;
  }
  t->q[t->n++] = ns;
  return 0;
}

static void *fwq_run(void *arg)
{
  struct fwq_thread *t = arg;
  cpu_set_t set;

  CPU_ZERO(&set);
  CPU_SET(t->cpu, &set);
  t->err = pthread_setaffinity_np(pthread_self(), sizeof(set), &set);
  pthread_barrier_wait(&g_start);
  if (t->err != 0)
    return NULL;
  for (;;) {
    uint64_t a = now_ns();
    uint64_t b;

    g_sink += fwq_work(t->work);
    b = now_ns();
    if (fwq_push(t, b - a) < 0) {
      t->err = ENOMEM;
      return NULL;
    }
    if (b >= t->deadline_ns)
      return NULL;
  }
}

static int parse_cpus(const char *list, int *cpus, int max)
{
  int n = 0;
  const char *p = list;

  while (*p != '\0') {
    char *end;
    long lo = strtol(p, &end, 10);
    long hi = lo;

    if (end == p)
      return -1;
    if (*end == '-') {
      p = end + 1;
      hi = strtol(p, &end, 10);
      if (end == p || hi < lo)
        return -1;
    }
    for (long c = lo; c <= hi; c++) {
      if (n >= max || c < 0 || c >= CPU_SETSIZE)
        return -1;
      cpus[n++] = (int)c;
    }
    p = end;
    if (*p == ',')
      p++;
    else if (*p != '\0')
      return -1;
  }
  return n;
}

/* Fastest quantum over at least 20 quanta and 300 ms: the undisturbed cost of `work`
 * iterations. The time floor keeps one competing timeslice from inflating every sample. */
static uint64_t best_of(uint64_t work)
{
  uint64_t best = UINT64_MAX;
  uint64_t end = now_ns() + 300000000ULL;

  for (int i = 0; i < 20 || now_ns() < end; i++) {
    uint64_t a = now_ns();
    uint64_t d;

    g_sink += fwq_work(work);
    d = now_ns() - a;
    if (d < best)
      best = d;
  }
  return best ? best : 1;
}

/* Double until a quantum is measurable, then rescale until it lands within 5 % of the
 * target (frequency ramp-up makes a single linear extrapolation overshoot or undershoot). */
static int calibrate(long target_us, int cpu)
{
  uint64_t work = 1024;
  uint64_t target = (uint64_t)target_us * 1000ULL;
  uint64_t got;
  uint64_t warm_end;

  if (target_us <= 0)
    return 2;
  if (cpu >= 0) {
    cpu_set_t set;

    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    if (pthread_setaffinity_np(pthread_self(), sizeof(set), &set) != 0) {
      fprintf(stderr, "fwq: cannot pin calibration to cpu %d\n", cpu);
      return 1;
    }
  }
  /* Spin ~200 ms so frequency scaling settles before timing. */
  warm_end = now_ns() + 200000000ULL;
  while (now_ns() < warm_end)
    g_sink += fwq_work(1024);
  while ((got = best_of(work)) < target / 4 && work < (1ULL << 40))
    work *= 2;
  for (int i = 0; i < 8; i++) {
    work = work * target / got;
    if (work == 0)
      work = 1;
    got = best_of(work);
    if (got * 20 >= target * 19 && got * 20 <= target * 21)
      break;
  }
  printf("%llu\n", (unsigned long long)work);
  return 0;
}

static int write_out(const char *dir, const struct fwq_thread *t)
{
  char path[4096];
  FILE *f;

  if (snprintf(path, sizeof(path), "%s/fwq_cpu%d.bin", dir, t->cpu) >= (int)sizeof(path))
    return -1;
  f = fopen(path, "wb");
  if (f == NULL)
    return -1;
  if (fwrite(t->q, sizeof(*t->q), t->n, f) != t->n) {
    fclose(f);
    return -1;
  }
  return fclose(f);
}

int main(int argc, char **argv)
{
  static int cpus[FWQ_MAX_CPUS];
  const char *cpulist = NULL;
  const char *outdir = NULL;
  long seconds = 0;
  uint64_t work = 0;
  int ncpu;
  struct fwq_thread *th;
  uint64_t deadline;
  long calibrate_us = 0;
  int rc = 0;

  for (int i = 1; i < argc; i++) {
    if (strcmp(argv[i], "--calibrate") == 0 && i + 1 < argc)
      calibrate_us = strtol(argv[++i], NULL, 10);
    else if (strcmp(argv[i], "-c") == 0 && i + 1 < argc)
      cpulist = argv[++i];
    else if (strcmp(argv[i], "-s") == 0 && i + 1 < argc)
      seconds = strtol(argv[++i], NULL, 10);
    else if (strcmp(argv[i], "-w") == 0 && i + 1 < argc)
      work = strtoull(argv[++i], NULL, 10);
    else if (strcmp(argv[i], "-o") == 0 && i + 1 < argc)
      outdir = argv[++i];
    else {
      fprintf(stderr, "usage: fwq --calibrate US [-c CPU] | fwq -c CPULIST -s SECONDS -w WORK -o DIR\n");
      return 2;
    }
  }
  if (calibrate_us != 0) {
    int cpu = -1;

    if (cpulist != NULL && parse_cpus(cpulist, cpus, FWQ_MAX_CPUS) > 0)
      cpu = cpus[0];
    return calibrate(calibrate_us, cpu);
  }
  if (cpulist == NULL || outdir == NULL || seconds <= 0 || work == 0) {
    fprintf(stderr, "fwq: -c, -s, -w and -o are required\n");
    return 2;
  }
  ncpu = parse_cpus(cpulist, cpus, FWQ_MAX_CPUS);
  if (ncpu <= 0) {
    fprintf(stderr, "fwq: bad CPU list %s\n", cpulist);
    return 2;
  }
  th = calloc((size_t)ncpu, sizeof(*th));
  if (th == NULL || pthread_barrier_init(&g_start, NULL, (unsigned)ncpu + 1) != 0) {
    fprintf(stderr, "fwq: out of memory\n");
    return 1;
  }
  deadline = now_ns() + (uint64_t)seconds * 1000000000ULL;
  for (int i = 0; i < ncpu; i++) {
    th[i].cpu = cpus[i];
    th[i].work = work;
    th[i].deadline_ns = deadline;
    if (pthread_create(&th[i].tid, NULL, fwq_run, &th[i]) != 0) {
      fprintf(stderr, "fwq: pthread_create failed\n");
      return 1;
    }
  }
  pthread_barrier_wait(&g_start);
  for (int i = 0; i < ncpu; i++) {
    pthread_join(th[i].tid, NULL);
    if (th[i].err != 0) {
      fprintf(stderr, "fwq: cpu %d: %s\n", th[i].cpu, strerror(th[i].err));
      rc = 1;
    } else if (write_out(outdir, &th[i]) != 0) {
      fprintf(stderr, "fwq: cannot write output for cpu %d\n", th[i].cpu);
      rc = 1;
    }
    free(th[i].q);
  }
  free(th);
  return rc;
}
