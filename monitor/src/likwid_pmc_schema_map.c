/*! \file likwid_pmc_schema_map.c
 *  Map LIKWID event/counter names onto host_cpu_hw schema keys.
 */

#include <ctype.h>
#include <stddef.h>
#include <string.h>

#include "likwid_pmc_schema_map.h"

#define LIKWID_INVALID_RESULT (1ULL << 63)

struct likwid_event_alias {
  const char *likwid;
  const char *schema;
};

/* Explicit aliases where LIKWID name != schema snake_case. */
static const struct likwid_event_alias g_event_aliases[] = {
  { "INSTR_RETIRED_ANY", "instr_retired_any" },
  { "CPU_CLK_UNHALTED_CORE", "cycles_unhalted_core" },
  { "CPU_CLK_UNHALTED_REF", "cycles_unhalted_ref" },
  { "MEM_INST_RETIRED_ALL_LOADS", "mem_load_uops_retired_l1_hit" },
  { "MEM_LOAD_UOPS_RETIRED_L1_HIT", "mem_load_uops_retired_l1_hit" },
  { "MEM_LOAD_UOPS_RETIRED_L2_HIT", "mem_load_uops_retired_l2_hit" },
  { "MEM_LOAD_UOPS_RETIRED_LLC_HIT", "mem_load_uops_retired_llc_hit" },
  { "L1D_REPLACEMENT", "l1d_replacement" },
  { "RETIRED_INSTRUCTIONS", "retired_instructions" },
  { "RETIRED_BRANCH_INSTR", "retired_branch_instr" },
  { "RETIRED_MISP_BRANCH_INSTR", "retired_misp_branch_instr" },
  { "LS_DISPATCH", "ls_dispatch" },
  { "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE", "fp_arith_inst_retired_scalar_double" },
  { "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE", "fp_arith_inst_retired_128b_packed_double" },
  { "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE", "fp_arith_inst_retired_256b_packed_double" },
  { "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE", "fp_arith_inst_retired_512b_packed_double" },
  { "FP_ARITH_INST_RETIRED_SCALAR_SINGLE", "fp_arith_inst_retired_scalar_single" },
  { "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE", "fp_arith_inst_retired_128b_packed_single" },
  { "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE", "fp_arith_inst_retired_256b_packed_single" },
  { "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE", "fp_arith_inst_retired_512b_packed_single" },
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

const char *likwid_pmc_schema_key_from_event(const char *event_name, char *buf,
					     size_t buflen)
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
