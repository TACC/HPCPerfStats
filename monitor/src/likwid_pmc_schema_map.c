/*! \file likwid_pmc_schema_map.c
 *  Map LIKWID event/counter names onto host_cpu_hw schema keys.
 */

#include <ctype.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#include "likwid_pmc_schema_map.h"
#include "schema.h"

#define LIKWID_INVALID_RESULT (1ULL << 63)

struct likwid_event_alias {
  const char *likwid;
  const char *schema;
};

/* Explicit aliases where LIKWID name != schema snake_case. */
static const struct likwid_event_alias g_event_aliases[] = {
    {"INSTR_RETIRED_ANY", "instr_retired_any"},
    {"CPU_CLK_UNHALTED_CORE", "cycles_unhalted_core"},
    {"CPU_CLK_UNHALTED_REF", "cycles_unhalted_ref"},
    {"MEM_INST_RETIRED_ALL_LOADS", "mem_load_uops_retired_l1_hit"},
    {"MEM_LOAD_UOPS_RETIRED_L1_HIT", "mem_load_uops_retired_l1_hit"},
    {"MEM_LOAD_UOPS_RETIRED_L2_HIT", "mem_load_uops_retired_l2_hit"},
    {"MEM_LOAD_UOPS_RETIRED_LLC_HIT", "mem_load_uops_retired_llc_hit"},
    {"L1D_REPLACEMENT", "l1d_replacement"},
    {"RETIRED_INSTRUCTIONS", "retired_instructions"},
    {"RETIRED_BRANCH_INSTR", "retired_branch_instr"},
    {"RETIRED_MISP_BRANCH_INSTR", "retired_misp_branch_instr"},
    {"LS_DISPATCH", "ls_dispatch"},
    {"LS_DISPATCH_ALL", "ls_dispatch"},
    {"CPU_CYCLES", "cpu_clock_est_cycles"},
    {"INST_RETIRED", "instr_retired"},
    {"FP_ARITH_INST_RETIRED_SCALAR_DOUBLE", "fp_arith_inst_retired_scalar_double"},
    {"FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE", "fp_arith_inst_retired_128b_packed_double"},
    {"FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE", "fp_arith_inst_retired_256b_packed_double"},
    {"FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE", "fp_arith_inst_retired_512b_packed_double"},
    {"FP_ARITH_INST_RETIRED_SCALAR_SINGLE", "fp_arith_inst_retired_scalar_single"},
    {"FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE", "fp_arith_inst_retired_128b_packed_single"},
    {"FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE", "fp_arith_inst_retired_256b_packed_single"},
    {"FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE", "fp_arith_inst_retired_512b_packed_single"},
};

static int str_eq_nocase(const char *a, const char *b)
{
  unsigned char ca, cb;

  if (a == NULL || b == NULL)
    return 0;
  while (*a != '\0' && *b != '\0') {
    ca = (unsigned char)*a++;
    cb = (unsigned char)*b++;
    if (tolower(ca) != tolower(cb))
      return 0;
  }
  return *a == '\0' && *b == '\0';
}

int likwid_pmc_result_is_invalid(unsigned long long val)
{
  return val == LIKWID_INVALID_RESULT;
}

int likwid_pmc_fixc_index(const char *counter_name)
{
  if (counter_name == NULL)
    return -1;
  if (str_eq_nocase(counter_name, "fixc0"))
    return 0;
  if (str_eq_nocase(counter_name, "fixc1"))
    return 1;
  if (str_eq_nocase(counter_name, "fixc2"))
    return 2;
  return -1;
}

const char *likwid_pmc_schema_key_from_event(const char *event_name, char *buf, size_t buflen)
{
  size_t i;
  size_t n;

  if (event_name == NULL || buf == NULL || buflen == 0)
    return NULL;

  for (i = 0; i < sizeof(g_event_aliases) / sizeof(g_event_aliases[0]); i++) {
    if (str_eq_nocase(event_name, g_event_aliases[i].likwid))
      return g_event_aliases[i].schema;
  }

  /* Default: lowercase copy (FP_ARITH_* and similar already match KEYS). */
  n = 0;
  while (event_name[n] != '\0' && n + 1 < buflen) {
    buf[n] = (char)tolower((unsigned char)event_name[n]);
    n++;
  }
  buf[n] = '\0';
  if (event_name[n] != '\0')
    return NULL;
  return buf;
}

static const char *const g_intel_only_keys[] = {
    "instr_retired_any",
    "cycles_unhalted_core",
    "cycles_unhalted_ref",
    "mem_load_uops_retired_l1_hit",
    "mem_load_uops_retired_l2_hit",
    "mem_load_uops_retired_llc_hit",
    "l1d_replacement",
    NULL,
};
static const char *const g_amd_only_keys[] = {
    "retired_instructions",
    "retired_branch_instr",
    "retired_misp_branch_instr",
    "ls_dispatch",
    NULL,
};

static int key_in(const char *const *list, const char *key)
{
  for (; *list != NULL; list++)
    if (strcmp(*list, key) == 0)
      return 1;
  return 0;
}

static int key_is_intel_only(const char *key)
{
  return key_in(g_intel_only_keys, key) || strncmp(key, "fp_arith_inst_retired_", 22) == 0;
}

static int key_is_arm_only(const char *key)
{
  return strncmp(key, "arm_", 4) == 0 || strncmp(key, "cpu_util_", 9) == 0 ||
         strncmp(key, "dram_chan", 9) == 0 || strcmp(key, "cpu_clock_est_cycles") == 0;
}

enum stats_sentinel likwid_pmc_unprogrammed_code(const char *key, int is_amd)
{
  if (key == NULL || key_is_arm_only(key) ||
      (is_amd ? key_is_intel_only(key) : key_in(g_amd_only_keys, key)))
    return STATS_SENTINEL_NOT_PRESENT;
  return STATS_SENTINEL_NOT_BUILT;
}

static void mark_programmed(struct schema *sc, unsigned char *codes, const char *key)
{
  int i = (key != NULL) ? schema_ref(sc, key) : -1;

  if (i >= 0)
    codes[i] = STATS_SENTINEL_OK;
}

/* One "EVENT:COUNTER" token: the event's own key plus the semantic copy the adapter publishes. */
static void mark_event_token(struct schema *sc, unsigned char *codes, char *token)
{
  static const char *const fixc_keys[] = {"instr_retired", "aperf", "mperf"};
  char keybuf[128];
  char *counter = strchr(token, ':');
  const char *key;
  int fixc;

  if (counter != NULL)
    *counter++ = '\0';
  fixc = likwid_pmc_fixc_index(counter);
  if (fixc >= 0)
    mark_programmed(sc, codes, fixc_keys[fixc]);
  key = likwid_pmc_schema_key_from_event(token, keybuf, sizeof(keybuf));
  if (key == NULL)
    return;
  mark_programmed(sc, codes, key);
  if (strcmp(key, "instr_retired_any") == 0 || strcmp(key, "retired_instructions") == 0)
    mark_programmed(sc, codes, "instr_retired");
  else if (strcmp(key, "cycles_unhalted_core") == 0)
    mark_programmed(sc, codes, "aperf");
  else if (strcmp(key, "cycles_unhalted_ref") == 0)
    mark_programmed(sc, codes, "mperf");
}

int likwid_pmc_key_codes(struct schema *sc, const char *event_string, int is_amd,
                         unsigned char *codes)
{
  char *copy;
  char *token;
  char *save = NULL;

  if (sc == NULL || codes == NULL)
    return -1;
  for (size_t k = 0; k < sc->sc_len; k++)
    codes[k] = (unsigned char)likwid_pmc_unprogrammed_code(sc->sc_ent[k]->se_key, is_amd);
  if (event_string == NULL || (copy = strdup(event_string)) == NULL)
    return event_string == NULL ? 0 : -1;
  for (token = strtok_r(copy, ",", &save); token != NULL; token = strtok_r(NULL, ",", &save))
    mark_event_token(sc, codes, token);
  free(copy);
  return 0;
}
