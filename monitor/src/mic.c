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

#define KEYS								\
  X(num_cores,   "c", "Number of cores"),				\
    X(threads_core, "c", "Number of threads per core"),			\
    X(user_sum,    "E,U=cs", "aggregate time in user mode"),			\
    X(nice_sum,    "E,U=cs", "aggregate time in user mode with low priority"),	\
    X(sys_sum,  "E,U=cs", "aggregate time in system mode"),		\
    X(idle_sum,    "E,U=cs", "aggregate time in idle task"),		\
    X(jiffy_counter, "E,U=cs", "Jiffy count at query time")
    
static void mic_collect_card(struct stats_type *type, char* card)
{
  struct mic_core_util *cutil = NULL;
  struct mic_device *mdh;
  uint32_t device_type;
  uint64_t idle_sum, nice_sum, sys_sum, user_sum, jiffy_counter;
  uint16_t num_cores, threads_core;

  if (mic_open_device(&mdh, atoi(card)) != E_MIC_SUCCESS) {
    fprintf(stderr, "Failed to open card %s: %s: %s\n",
	    card, mic_get_error_string(), strerror(errno));
    goto out;
  }

  if (mic_get_device_type(mdh, &device_type) != E_MIC_SUCCESS) {
    fprintf(stderr, "%s: Failed to get device type: %s: %s\n",
	    mic_get_device_name(mdh),
	    mic_get_error_string(), strerror(errno));
    goto out;
  }

  if (device_type != KNC_ID) {
    fprintf(stderr, "Unknown device Type: %u\n", device_type);
    goto out;
  }

  printf("Found KNC device '%s'\n", mic_get_device_name(mdh));

  if (mic_alloc_core_util(&cutil) != E_MIC_SUCCESS) {
    fprintf(
	    stderr,
	    "%s: Failed to allocate Core utilization information: %s: %s\n",
	    mic_get_device_name(mdh),
	    mic_get_error_string(),
	    strerror(errno));
    goto out;
  }

  if (mic_update_core_util(mdh, cutil) != E_MIC_SUCCESS) {
    fprintf(
	    stderr,
	    "%s: Failed to update Core utilization information: %s: %s\n",
	    mic_get_device_name(mdh),
	    mic_get_error_string(),
	    strerror(errno));
    goto out;
  }

  struct stats *stats = get_current_stats(type, card);
  if (stats == NULL)
    goto out;


#define X(k,r...)						\
    ({								\
      if (mic_get_##k(cutil, &k) != E_MIC_SUCCESS) {		\
	ERROR("%s: Failed to get the number of k : %s: %s\n",	\
	  mic_get_device_name(mdh),				\
	      mic_get_error_string(),				\
	      strerror(errno));					\
      }								\
      else							\
	stats_set(stats, #k, k);				\
  })
  KEYS;  
#undef X
  /* 
  if (mic_get_num_cores(cutil, &num_cores) != E_MIC_SUCCESS) {
    fprintf(stderr,
	    "%s: Failed to get the number of cores : %s: %s\n",
	    mic_get_device_name(mdh),
	    mic_get_error_string(),
	    strerror(errno));
    goto out;
  }

  printf("Number of cores : %u\n", num_cores);

  if (mic_get_threads_core(cutil, &thread_core) != E_MIC_SUCCESS) {
    fprintf(
	    stderr,
	    "%s: Failed to get the Number of threads per core : %s: %s\n",
	    mic_get_device_name(mdh),
	    mic_get_error_string(),
	    strerror(errno));
    goto out;
  }

  printf("Number of threads per core : %u\n", thread_core);

  if (mic_get_jiffy_counter(cutil, &jiffy_counter) != E_MIC_SUCCESS) {
    fprintf(stderr, "%s: Failed to get jiffy count : %s: %s\n",
	    mic_get_device_name(mdh),
	    mic_get_error_string(),
	    strerror(errno));
    goto out;
  }

  printf("Jiffy count at Query time : %d\n", (int)jiffy_counter);

  if (mic_get_idle_sum(cutil, &idle_sum) != E_MIC_SUCCESS) {
    fprintf(stderr, "%s: Failed to get the idle sum: %s: %s\n",
	    mic_get_device_name(mdh),
	    mic_get_error_string(),
	    strerror(errno));
    goto out;
  }
  printf("Idle Sum: %d \n", (int)idle_sum);

  if (mic_get_nice_sum(cutil, &nice_sum) != E_MIC_SUCCESS) {
    fprintf(stderr, "%s: Failed to get the nice sum: %s: %s\n",
	    mic_get_device_name(mdh),
	    mic_get_error_string(),
	    strerror(errno));
    goto out;
  }
  printf("Nice Sum : %d\n", (int)nice_sum);

  if (mic_get_sys_sum(cutil, &sys_sum) != E_MIC_SUCCESS) {
    fprintf(stderr, "%s: Failed to get the system sum : %s: %s\n",
	    mic_get_device_name(mdh),
	    mic_get_error_string(),
	    strerror(errno));
    goto out;
  }
  printf("System Sum : %d\n", (int)sys_sum);
  
  if (mic_get_user_sum(cutil, &user_sum) != E_MIC_SUCCESS) {
    fprintf(stderr, "%s: Failed to get the user sum : %s: %s\n",
	    mic_get_device_name(mdh),
	    mic_get_error_string(),
	    strerror(errno));
    goto out;
  }
  printf("User Sum : %d\n\n", (int)user_sum);
  */

 out:
  if (cutil != NULL)
    (void)mic_free_core_util(cutil);
  (void)mic_close_device(mdh);
}


static void mic_collect(struct stats_type *type)
{
  int ncards, card_num, card;
  struct mic_devices_list *mdl;
  char c[80];

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

  printf("Number of cards : %d\n",ncards);

  for (card_num = 0; card_num < ncards; card_num++)
    {    
      if (mic_get_device_at_index(mdl, card_num, &card) != E_MIC_SUCCESS) {
	fprintf(stderr, "Failed to get card at index %d: %s: %s\n",
		card_num, mic_get_error_string(), strerror(errno));
	goto out;
      }	    
      snprintf(c, sizeof(c), "%d", card);
      mic_collect_card(type, c);
    }
 out:
  (void)mic_free_devices(mdl);
}


struct stats_type mic_stats_type = {
  .st_name = "host_mic",
  .st_collect = &mic_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
