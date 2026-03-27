#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <ctype.h>
#include "stats.h"
#include "trace.h"
#include "cpuid.h"
#include "variorum_rapl.h"

#ifdef HAVE_VARIORUM
#include <variorum.h>
#endif

static int parse_json_number(const char *start, const char *end, double *value)
{
  const char *p = start;
  while (p < end && *p != ':')
    p++;
  if (p >= end)
    return -1;
  p++;
  while (p < end && isspace((unsigned char)*p))
    p++;
  if (p >= end)
    return -1;
  {
    char *next = NULL;
    double parsed = strtod(p, &next);
    if (next == p || next > end)
      return -1;
    *value = parsed;
  }
  return 0;
}

static int find_key_in_range(const char *json, const char *range_end,
                             const char *key, double *value)
{
  const char *cursor = json;
  size_t key_len = strlen(key);
  while (cursor < range_end) {
    const char *found = strstr(cursor, key);
    if (found == NULL || found >= range_end)
      break;
    if (parse_json_number(found + key_len, range_end, value) == 0)
      return 0;
    cursor = found + key_len;
  }
  return -1;
}

static int find_socket_range(const char *json, unsigned int socket_id,
                             const char **socket_start, const char **socket_end)
{
  char socket_key[64];
  const char *p = NULL;
  int depth = 0;
  snprintf(socket_key, sizeof(socket_key), "\"Socket_%u\"", socket_id);
  p = strstr(json, socket_key);
  if (p == NULL) {
    snprintf(socket_key, sizeof(socket_key), "\"socket_%u\"", socket_id);
    p = strstr(json, socket_key);
  }
  if (p == NULL)
    return -1;
  p = strchr(p, '{');
  if (p == NULL)
    return -1;
  *socket_start = p;
  while (*p != '\0') {
    if (*p == '{')
      depth++;
    else if (*p == '}') {
      depth--;
      if (depth == 0) {
        *socket_end = p;
        return 0;
      }
    }
    p++;
  }
  return -1;
}

static int to_millijoules(double joules, unsigned long long *mj)
{
  if (joules < 0.0)
    return -1;
  *mj = (unsigned long long) (joules * 1000.0);
  return 0;
}

static int parse_socket_energy(const char *json, unsigned int socket_id,
                               unsigned long long *pkg_mj,
                               unsigned long long *core_mj,
                               unsigned long long *dram_mj,
                               int *has_pkg,
                               int *has_core,
                               int *has_dram)
{
  const char *socket_start = NULL;
  const char *socket_end = NULL;
  char socket_key[128];
  double value = 0.0;
  *has_pkg = 0;
  *has_core = 0;
  *has_dram = 0;
  if (find_socket_range(json, socket_id, &socket_start, &socket_end) == 0) {
    if (find_key_in_range(socket_start, socket_end, "\"package_joules\"", &value) == 0 &&
        to_millijoules(value, pkg_mj) == 0)
      *has_pkg = 1;
    if (find_key_in_range(socket_start, socket_end, "\"pkg_joules\"", &value) == 0 &&
        to_millijoules(value, pkg_mj) == 0)
      *has_pkg = 1;
    if (find_key_in_range(socket_start, socket_end, "\"core_joules\"", &value) == 0 &&
        to_millijoules(value, core_mj) == 0)
      *has_core = 1;
    if (find_key_in_range(socket_start, socket_end, "\"cores_joules\"", &value) == 0 &&
        to_millijoules(value, core_mj) == 0)
      *has_core = 1;
    if (find_key_in_range(socket_start, socket_end, "\"dram_joules\"", &value) == 0 &&
        to_millijoules(value, dram_mj) == 0)
      *has_dram = 1;
    if (find_key_in_range(socket_start, socket_end, "\"memory_joules\"", &value) == 0 &&
        to_millijoules(value, dram_mj) == 0)
      *has_dram = 1;
  }
  snprintf(socket_key, sizeof(socket_key), "\"cpu_socket_%u_joules\"", socket_id);
  if (find_key_in_range(json, json + strlen(json), socket_key, &value) == 0 &&
      to_millijoules(value, pkg_mj) == 0)
    *has_pkg = 1;
  snprintf(socket_key, sizeof(socket_key), "\"package_socket_%u_joules\"", socket_id);
  if (find_key_in_range(json, json + strlen(json), socket_key, &value) == 0 &&
      to_millijoules(value, pkg_mj) == 0)
    *has_pkg = 1;
  snprintf(socket_key, sizeof(socket_key), "\"core_socket_%u_joules\"", socket_id);
  if (find_key_in_range(json, json + strlen(json), socket_key, &value) == 0 &&
      to_millijoules(value, core_mj) == 0)
    *has_core = 1;
  snprintf(socket_key, sizeof(socket_key), "\"dram_socket_%u_joules\"", socket_id);
  if (find_key_in_range(json, json + strlen(json), socket_key, &value) == 0 &&
      to_millijoules(value, dram_mj) == 0)
    *has_dram = 1;
  return (*has_pkg || *has_core || *has_dram) ? 0 : -1;
}

int variorum_rapl_parse_socket_mj(const char *energy_json,
                                  unsigned int socket_id,
                                  unsigned long long *pkg_mj,
                                  unsigned long long *core_mj,
                                  unsigned long long *dram_mj,
                                  int *has_pkg,
                                  int *has_core,
                                  int *has_dram)
{
  if (energy_json == NULL || pkg_mj == NULL || core_mj == NULL ||
      dram_mj == NULL || has_pkg == NULL || has_core == NULL ||
      has_dram == NULL)
    return -1;
  return parse_socket_energy(energy_json, socket_id, pkg_mj, core_mj, dram_mj,
                             has_pkg, has_core, has_dram);
}

int variorum_rapl_is_supported_processor(void)
{
  switch (processor) {
  case AMD_17H:
  case AMD_19H:
  case SANDYBRIDGE:
  case IVYBRIDGE:
  case HASWELL:
  case BROADWELL:
  case SKYLAKE:
    return 1;
  default:
    return 0;
  }
}

int variorum_rapl_collect_socket_mj(unsigned int socket_id,
                                    unsigned long long *pkg_mj,
                                    unsigned long long *core_mj,
                                    unsigned long long *dram_mj,
                                    int *has_pkg,
                                    int *has_core,
                                    int *has_dram)
{
#ifdef HAVE_VARIORUM
  int rc = -1;
  char *energy_json = NULL;
  if (pkg_mj == NULL || core_mj == NULL || dram_mj == NULL ||
      has_pkg == NULL || has_core == NULL || has_dram == NULL)
    return -1;
  *pkg_mj = 0;
  *core_mj = 0;
  *dram_mj = 0;
  *has_pkg = 0;
  *has_core = 0;
  *has_dram = 0;
  rc = variorum_get_energy_json(&energy_json);
  if (rc != 0 || energy_json == NULL) {
    TRACE("variorum_get_energy_json failed for socket %u\n", socket_id);
    free(energy_json);
    return -1;
  }
  rc = variorum_rapl_parse_socket_mj(energy_json, socket_id, pkg_mj, core_mj,
                                     dram_mj, has_pkg, has_core, has_dram);
  free(energy_json);
  return rc;
#else
  (void) socket_id;
  (void) pkg_mj;
  (void) core_mj;
  (void) dram_mj;
  (void) has_pkg;
  (void) has_core;
  (void) has_dram;
  return -1;
#endif
}
