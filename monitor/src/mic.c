/* host_mic — Intel Xeon Phi (KNC) core utilization via MPSS miclib. */
#include <stdio.h>
#include <errno.h>
#include <limits.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include "miclib.h"
#include "stats.h"
#include "collect.h"
#include "trace.h"
#include "string1.h"

#define KEYS \
  X(num_cores, "c", "Number of cores"), \
  X(threads_core, "c", "Number of threads per core"), \
  X(user_sum, "E,U=cs", "aggregate time in user mode"), \
  X(nice_sum, "E,U=cs", "aggregate time in user mode with low priority"), \
  X(sys_sum, "E,U=cs", "aggregate time in system mode"), \
  X(idle_sum, "E,U=cs", "aggregate time in idle task"), \
  X(jiffy_counter, "E,U=cs", "Jiffy count at query time")

static int mic_open_knc_device(const char *card, struct mic_device **mdh_out)
{
  struct mic_device *mdh = NULL;
  uint32_t device_type;

  if (card == NULL || mdh_out == NULL)
    return -1;
  *mdh_out = NULL;

  if (mic_open_device(&mdh, atoi(card)) != E_MIC_SUCCESS) {
    fprintf(stderr, "Failed to open card %s: %s: %s\n",
            card, mic_get_error_string(), strerror(errno));
    return -1;
  }

  if (mic_get_device_type(mdh, &device_type) != E_MIC_SUCCESS) {
    fprintf(stderr, "%s: Failed to get device type: %s: %s\n",
            mic_get_device_name(mdh), mic_get_error_string(), strerror(errno));
    mic_close_device(mdh);
    return -1;
  }

  if (device_type != KNC_ID) {
    fprintf(stderr, "Unknown device Type: %u\n", device_type);
    mic_close_device(mdh);
    return -1;
  }

  *mdh_out = mdh;
  return 0;
}

static struct mic_core_util *mic_alloc_and_update_util(struct mic_device *mdh)
{
  struct mic_core_util *cutil = NULL;

  if (mdh == NULL)
    return NULL;

  if (mic_alloc_core_util(&cutil) != E_MIC_SUCCESS) {
    fprintf(stderr, "%s: Failed to allocate Core utilization information: %s: %s\n",
            mic_get_device_name(mdh), mic_get_error_string(), strerror(errno));
    return NULL;
  }

  if (mic_update_core_util(mdh, cutil) != E_MIC_SUCCESS) {
    fprintf(stderr, "%s: Failed to update Core utilization information: %s: %s\n",
            mic_get_device_name(mdh), mic_get_error_string(), strerror(errno));
    mic_free_core_util(cutil);
    return NULL;
  }
  return cutil;
}

static void mic_publish_core_keys(struct stats *stats, struct mic_device *mdh,
                                  struct mic_core_util *cutil)
{
  uint64_t idle_sum;
  uint64_t nice_sum;
  uint64_t sys_sum;
  uint64_t user_sum;
  uint64_t jiffy_counter;
  uint16_t num_cores;
  uint16_t threads_core;

#define X(k, r...) \
  do { \
    if (mic_get_##k(cutil, &k) != E_MIC_SUCCESS) { \
      ERROR("%s: Failed to get " #k ": %s: %s\n", \
            mic_get_device_name(mdh), mic_get_error_string(), strerror(errno)); \
    } else { \
      stats_set(stats, #k, k); \
    } \
  } while (0)
  KEYS;
#undef X
}

static void mic_collect_card(struct stats_type *type, char *card)
{
  struct mic_core_util *cutil = NULL;
  struct mic_device *mdh = NULL;
  struct stats *stats;

  if (type == NULL || card == NULL)
    return;
  if (mic_open_knc_device(card, &mdh) != 0)
    goto out;

  printf("Found KNC device '%s'\n", mic_get_device_name(mdh));

  cutil = mic_alloc_and_update_util(mdh);
  if (cutil == NULL)
    goto out;

  stats = get_current_stats(type, card);
  if (stats == NULL)
    goto out;

  mic_publish_core_keys(stats, mdh, cutil);

 out:
  if (cutil != NULL)
    (void) mic_free_core_util(cutil);
  if (mdh != NULL)
    (void) mic_close_device(mdh);
}

static void mic_collect(struct stats_type *type)
{
  int ncards;
  int card_num;
  int card;
  struct mic_devices_list *mdl = NULL;
  char c[80];

  if (type == NULL)
    return;

  if (mic_get_devices(&mdl) != E_MIC_SUCCESS) {
    fprintf(stderr, "Failed to get cards list: %s: %s\n",
            mic_get_error_string(), strerror(errno));
    goto out;
  }

  if (mic_get_ndevices(mdl, &ncards) != E_MIC_SUCCESS) {
    fprintf(stderr, "Failed to get number of cards: %s: %s\n",
            mic_get_error_string(), strerror(errno));
    goto out;
  }

  if (ncards == 0) {
    fprintf(stderr, "No MIC card found\n");
    goto out;
  }

  printf("Number of cards : %d\n", ncards);

  for (card_num = 0; card_num < ncards; card_num++) {
    if (mic_get_device_at_index(mdl, card_num, &card) != E_MIC_SUCCESS) {
      fprintf(stderr, "Failed to get card at index %d: %s: %s\n",
              card_num, mic_get_error_string(), strerror(errno));
      goto out;
    }
    snprintf(c, sizeof(c), "%d", card);
    mic_collect_card(type, c);
  }

 out:
  if (mdl != NULL)
    (void) mic_free_devices(mdl);
}

struct stats_type mic_stats_type = {
  .st_name = "host_mic",
  .st_collect = &mic_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
