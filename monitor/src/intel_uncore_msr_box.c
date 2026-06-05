/*! \file intel_uncore_msr_box.c
 *  MSR-based Intel CBo/CHA uncore box programming and collect.
 */

#include "intel_uncore_msr_box.h"

#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "path_open_fail_once.h"
#include "stats.h"
#include "trace.h"

/* -------- SNB / IVB CBo (offset stride 32) ---------- */

#define SNB_CBOX_CTL0	  0xD04
#define SNB_CBOX_FILTER0  0xD14
#define SNB_CTL0	  0xD10
#define SNB_CTR0	  0xD16

static uint64_t snb_ivb_cbox_filter(void)
{
  return (uint64_t)((0x0ULL << 0) | (0x00ULL << 10) | (0x1FULL << 18)
        | (0x000ULL << 23));
}

int intel_uncore_cbo_snb_ivb_begin_box(char *cpu, int box, uint64_t *events,
               size_t nr_events)
{
  int rc = -1;
  char msr_path[80];
  int msr_fd = -1;
  uint64_t ctl;
  uint64_t filter;
  int offset = box * 32;
  size_t i;

  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  if (path_open_is_skipped(msr_path))
    goto out;
  msr_fd = open(msr_path, O_RDWR);
  if (msr_fd < 0) {
    path_open_record_failure_once(msr_path);
    goto out;
  }

  ctl = 0x10100ULL;
  if (pwrite(msr_fd, &ctl, sizeof(ctl), SNB_CBOX_CTL0 + offset) < 0) {
    ERROR("cannot enable freeze of CBo counter: %m\n");
    goto out;
  }

  filter = snb_ivb_cbox_filter();
  if (pwrite(msr_fd, &filter, sizeof(filter), SNB_CBOX_FILTER0 + offset)
      < 0) {
    ERROR("cannot modify CBo filters: %m\n");
    goto out;
  }

  for (i = 0; i < nr_events; i++) {
    TRACE("MSR %08X, event %016llX\n",
    SNB_CTL0 + offset + (int)i,
    (unsigned long long)events[i]);
    if (pwrite(msr_fd, &events[i], sizeof(events[i]),
         SNB_CTL0 + offset + (int)i)
  < 0) {
      ERROR("cannot write event %016llX to MSR %08X through `%s': %m\n",
      (unsigned long long)events[i],
      (unsigned)(SNB_CTL0 + offset + (int)i), msr_path);
      goto out;
    }
  }

  ctl = 0x10000ULL;
  if (pwrite(msr_fd, &ctl, sizeof(ctl), SNB_CBOX_CTL0 + offset) < 0) {
    ERROR("cannot unfreeze CBo counters: %m\n");
    goto out;
  }

  rc = 0;

out:
  if (msr_fd >= 0)
    close(msr_fd);

  return rc;
}

void intel_uncore_cbo_snb_ivb_collect_box(struct stats_type *type, char *cpu,
            int pkg_id, int box,
            const char *const ctr_keys[4])
{
  struct stats *stats = NULL;
  char msr_path[80];
  int msr_fd = -1;
  int offset = 32 * box;
  char pkg_box[80];
  int i;

  snprintf(pkg_box, sizeof(pkg_box), "%d/%d", pkg_id, box);
  TRACE("cpu %s\n", cpu);
  TRACE("pkg_id/box %s\n", pkg_box);
  stats = get_current_stats(type, pkg_box);
  if (stats == NULL)
    goto out;

  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  if (path_open_is_skipped(msr_path))
    goto out;
  msr_fd = open(msr_path, O_RDONLY);
  if (msr_fd < 0) {
    path_open_record_failure_once(msr_path);
    goto out;
  }

  for (i = 0; i < 4; i++) {
    uint64_t val = 0;
    unsigned addr = SNB_CTR0 + offset + i;
    const char *key = (ctr_keys != NULL) ? ctr_keys[i] : NULL;

    if (pread(msr_fd, &val, sizeof(val), addr) < 0)
      ERROR("cannot read `%s' (%08X) through `%s': %m\n", key, addr,
      msr_path);
    else if (key != NULL)
      stats_set(stats, key, val);
  }

out:
  if (msr_fd >= 0)
    close(msr_fd);
}

/* -------- HSW / BDW CBo (offset stride 16) ---------- */

#define HSW_CBOX_CTL0	   0xE00
#define HSW_CBOX_FILTER0_0 0xE05
#define HSW_CBOX_FILTER1_0 0xE06
#define HSW_CTL0	   0xE01
#define HSW_CTR0	   0xE08

static uint64_t hsw_bdw_cbox_filter0(void)
{
  return (uint64_t)((0x0ULL << 0) | (0x00ULL << 10) | (0x3FULL << 18)
        | (0x000ULL << 23));
}

static uint64_t hsw_bdw_cbox_filter1(void)
{
  return (uint64_t)((0x0000ULL << 0) | (0x00ULL << 20) | (0x0ULL << 30)
        | (0x0ULL << 31));
}

int intel_uncore_cbo_hsw_bdw_begin_box(char *cpu, int box, uint64_t *events,
               size_t nr_events)
{
  int rc = -1;
  char msr_path[80];
  int msr_fd = -1;
  uint64_t ctl;
  uint64_t filter;
  int offset = box * 16;
  size_t i;

  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  if (path_open_is_skipped(msr_path))
    goto out;
  msr_fd = open(msr_path, O_RDWR);
  if (msr_fd < 0) {
    path_open_record_failure_once(msr_path);
    goto out;
  }

  ctl = 0x00100ULL;
  if (pwrite(msr_fd, &ctl, sizeof(ctl), HSW_CBOX_CTL0 + offset) < 0) {
    ERROR("cannot enable freeze of CBo counter %d: %m\n", box);
    goto out;
  }

  filter = hsw_bdw_cbox_filter0();
  if (pwrite(msr_fd, &filter, sizeof(filter), HSW_CBOX_FILTER0_0 + offset)
      < 0) {
    ERROR("cannot modify CBo Filter 0 : %m\n");
    goto out;
  }
  filter = hsw_bdw_cbox_filter1();
  if (pwrite(msr_fd, &filter, sizeof(filter), HSW_CBOX_FILTER1_0 + offset)
      < 0) {
    ERROR("cannot modify CBo Filter 1: %m\n");
    goto out;
  }

  for (i = 0; i < nr_events; i++) {
    TRACE("MSR %08X, event %016llX\n",
    HSW_CTL0 + offset + (int)i,
    (unsigned long long)events[i]);
    if (pwrite(msr_fd, &events[i], sizeof(events[i]),
         HSW_CTL0 + offset + (int)i)
  < 0) {
      ERROR("cannot write event %016llX to MSR %08X through `%s': %m\n",
      (unsigned long long)events[i],
      (unsigned)(HSW_CTL0 + offset + (int)i), msr_path);
      goto out;
    }
  }

  ctl = 0x00000ULL;
  if (pwrite(msr_fd, &ctl, sizeof(ctl), HSW_CBOX_CTL0 + offset) < 0) {
    ERROR("cannot unfreeze CBo counters: %m\n");
    goto out;
  }

  rc = 0;

out:
  if (msr_fd >= 0)
    close(msr_fd);

  return rc;
}

void intel_uncore_cbo_hsw_bdw_collect_box(struct stats_type *type, char *cpu,
             int pkg_id, int box,
             const char *const ctr_keys[4])
{
  struct stats *stats = NULL;
  char msr_path[80];
  int msr_fd = -1;
  int offset = 16 * box;
  char pkg_box[80];
  int i;

  snprintf(pkg_box, sizeof(pkg_box), "%d/%d", pkg_id, box);
  TRACE("cpu %s\n", cpu);
  TRACE("pkg_id/box %s\n", pkg_box);
  stats = get_current_stats(type, pkg_box);
  if (stats == NULL)
    goto out;

  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  if (path_open_is_skipped(msr_path))
    goto out;
  msr_fd = open(msr_path, O_RDONLY);
  if (msr_fd < 0) {
    path_open_record_failure_once(msr_path);
    goto out;
  }

  for (i = 0; i < 4; i++) {
    uint64_t val = 0;
    unsigned addr = HSW_CTR0 + offset + i;
    const char *key = (ctr_keys != NULL) ? ctr_keys[i] : NULL;

    if (pread(msr_fd, &val, sizeof(val), addr) < 0)
      ERROR("cannot read `%s' (%08X) through `%s': %m\n", key, addr,
      msr_path);
    else if (key != NULL)
      stats_set(stats, key, val);
  }

out:
  if (msr_fd >= 0)
    close(msr_fd);
}

/* -------- SKX CHA (stride 16; extra global unfreeze MSR) ---------- */

#define SKX_CHA_GLOBAL_UNFREEZE_MSR 0x0700
#define SKX_CHA_CTL0		      0xE00
#define SKX_CHA_FILTER0_0	      0xE05
#define SKX_CHA_FILTER1_0	      0xE06
#define SKX_CTL0		      0xE01
#define SKX_CTR0		      0xE08

int intel_uncore_cha_skx_begin_box(char *cpu, int box, uint64_t *events,
           size_t nr_events)
{
  int rc = -1;
  char msr_path[80];
  int msr_fd = -1;
  uint64_t ctl;
  uint64_t filter;
  int offset = box * 16;
  size_t i;

  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  if (path_open_is_skipped(msr_path))
    goto out;
  msr_fd = open(msr_path, O_RDWR);
  if (msr_fd < 0) {
    path_open_record_failure_once(msr_path);
    goto out;
  }

  ctl = 1ULL << 61;
  if (pwrite(msr_fd, &ctl, sizeof(ctl), SKX_CHA_GLOBAL_UNFREEZE_MSR) < 0) {
    ERROR("cannot enable freeze of CHA counter %d: %m\n", box);
    goto out;
  }

  ctl = 0x00100ULL;
  if (pwrite(msr_fd, &ctl, sizeof(ctl), SKX_CHA_CTL0 + offset) < 0) {
    ERROR("cannot enable freeze of CHA counter %d: %m\n", box);
    goto out;
  }

  filter = 0x01e20000;
  if (pwrite(msr_fd, &filter, sizeof(filter), SKX_CHA_FILTER0_0 + offset)
      < 0) {
    ERROR("cannot modify CHA Filter 0 : %m\n");
    goto out;
  }
  filter = 0x3b;
  if (pwrite(msr_fd, &filter, sizeof(filter), SKX_CHA_FILTER1_0 + offset)
      < 0) {
    ERROR("cannot modify CHA Filter 1: %m\n");
    goto out;
  }

  for (i = 0; i < nr_events; i++) {
    TRACE("MSR %08X, event %016llX\n",
    SKX_CTL0 + offset + (int)i,
    (unsigned long long)events[i]);
    if (pwrite(msr_fd, &events[i], sizeof(events[i]),
         SKX_CTL0 + offset + (int)i)
  < 0) {
      ERROR("cannot write event %016llX to MSR %08X through `%s': %m\n",
      (unsigned long long)events[i],
      (unsigned)(SKX_CTL0 + offset + (int)i), msr_path);
      goto out;
    }
  }

  ctl = 0x00000ULL;
  if (pwrite(msr_fd, &ctl, sizeof(ctl), SKX_CHA_CTL0 + offset) < 0) {
    ERROR("cannot unfreeze CBo counters: %m\n");
    goto out;
  }

  rc = 0;

out:
  if (msr_fd >= 0)
    close(msr_fd);

  return rc;
}

void intel_uncore_cha_skx_collect_box(struct stats_type *type, char *cpu,
               int pkg_id, int box,
               const char *const ctr_keys[4])
{
  struct stats *stats = NULL;
  char msr_path[80];
  int msr_fd = -1;
  int offset = 16 * box;
  char pkg_box[80];
  int i;

  snprintf(pkg_box, sizeof(pkg_box), "%d/%d", pkg_id, box);
  TRACE("cpu %s\n", cpu);
  TRACE("pkg_id/box %s\n", pkg_box);
  stats = get_current_stats(type, pkg_box);
  if (stats == NULL)
    goto out;

  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  if (path_open_is_skipped(msr_path))
    goto out;
  msr_fd = open(msr_path, O_RDONLY);
  if (msr_fd < 0) {
    path_open_record_failure_once(msr_path);
    goto out;
  }

  for (i = 0; i < 4; i++) {
    uint64_t val = 0;
    unsigned addr = SKX_CTR0 + offset + i;
    const char *key = (ctr_keys != NULL) ? ctr_keys[i] : NULL;

    if (pread(msr_fd, &val, sizeof(val), addr) < 0)
      ERROR("cannot read `%s' (%08X) through `%s': %m\n", key, addr,
      msr_path);
    else if (key != NULL)
      stats_set(stats, key, val);
  }

out:
  if (msr_fd >= 0)
    close(msr_fd);
}
