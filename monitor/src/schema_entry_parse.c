/* Parse one schema entry string: key,opt1[=arg],opt2,... */
#include <ctype.h>
#include <malloc.h>
#include <stdlib.h>
#include <string.h>

#include "schema.h"
#include "string1.h"
#include "trace.h"

static void schema_apply_one_option(struct schema_entry *se, char *opt)
{
  char *opt_arg = opt;

  if (se == NULL || opt == NULL)
    return;

  strsep(&opt_arg, "=");

  switch (toupper((unsigned char) *opt)) {
  default:
    TRACE("unknown schema option `%s'\n", opt);
    break;
  case 'C':
    se->se_type = SE_CONTROL;
    break;
  case 'E':
    se->se_type = SE_EVENT;
    break;
  case 'U':
    if (opt_arg != NULL)
      se->se_unit = strdup(opt_arg);
    break;
  case 'W':
    if (opt_arg != NULL)
      se->se_width = (unsigned int) strtoul(opt_arg, NULL, 0);
    break;
  }
}

static int schema_copy_entry_key(struct schema_entry *se, const char *key)
{
  size_t key_len = strlen(key);
  int n;

  n = snprintf(se->se_key, key_len + 1, "%s", key);
  return (n >= 0 && (size_t) n <= key_len) ? 0 : -1;
}

struct schema_entry *parse_schema_entry(char *str)
{
  char *key;
  size_t key_len;
  struct schema_entry *se;

  if (str == NULL)
    return NULL;

  while (isspace((unsigned char) *str))
    str++;

  key = strsep(&str, ",");
  if (key == NULL || *key == '\0')
    return NULL;

  key_len = strlen(key);
  se = (struct schema_entry *) malloc(sizeof(*se) + key_len + 1);
  if (se == NULL)
    return NULL;

  memset(se, 0, sizeof(*se));
  if (schema_copy_entry_key(se, key) < 0) {
    free(se);
    return NULL;
  }

  while (str != NULL) {
    char *opt = strsep(&str, ",");

    if (*opt == '\0') {
      /* Allow a single trailing comma (e.g. generated "key,"). */
      if (str == NULL)
        continue;
      free(se->se_unit);
      free(se);
      return NULL;
    }
    schema_apply_one_option(se, opt);
  }

  TRACE("se_key `%s', se_type %u, se_width %u, se_unit %s, se_desc `%s'\n",
        se->se_key, se->se_type, se->se_width,
        se->se_unit ? se->se_unit : "none",
        se->se_desc ? se->se_desc : "none");

  return se;
}
