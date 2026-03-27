#include <ctype.h>
#include <malloc.h>
#include <stdlib.h>
#include <string.h>

#include "schema.h"
#include "string1.h"
#include "trace.h"

/* key,opt1[=arg],opt2,... */

static void schema_apply_one_option(struct schema_entry *se, char *opt)
{
  char *opt_arg = opt;
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

struct schema_entry *parse_schema_entry(char *str)
{
  while (isspace((unsigned char) *str))
    str++;

  char *key = strsep(&str, ",");
  if (*key == 0)
    return NULL;

  struct schema_entry *se = (struct schema_entry *) malloc(sizeof(*se) + strlen(key) + 1);
  if (se == NULL)
    return NULL;

  memset(se, 0, sizeof(*se));
  strcpy(se->se_key, key);

  while (str != NULL) {
    char *opt = strsep(&str, ",");
    if (*opt == 0)
      continue;
    schema_apply_one_option(se, opt);
  }

  TRACE("se_key `%s', se_type %u, se_width %u, se_unit %s, se_desc `%s'\n",
	se->se_key, se->se_type, se->se_width,
	se->se_unit ? : "NONE", se->se_desc ? : "NONE");

  return se;
}
