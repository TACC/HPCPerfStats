#include <malloc.h>
#include <ctype.h>
#include "stats.h"
#include "dict.h"
#include "trace.h"
#include "schema.h"
#include "string1.h"

/* key,opt1[=arg],opt2,... */

struct schema_entry *parse_schema_entry(char *str)
{
  struct schema_entry *se = NULL;

  while (isspace(*str))
    str++;

  char *key = strsep(&str, ",");
  if (*key == 0)
    return NULL;

  se = (struct schema_entry *) malloc(sizeof(*se) + strlen(key) + 1);
  if (se == NULL)
    return NULL;

  memset(se, 0, sizeof(*se));
  strcpy(se->se_key, key);

  while (str != NULL) {
    char *opt = strsep(&str, ",");
    if (*opt == 0)
      continue;

    char *opt_arg = opt;
    strsep(&opt_arg, "=");

    switch (toupper(*opt)) { /* XXX toupper() */
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
        se->se_width = strtoul(opt_arg, NULL, 0);
      break;
    }
  }

  TRACE("se_key `%s', se_type %u, se_width %u, se_unit %s, se_desc `%s'\n",
        se->se_key, se->se_type, se->se_width,
        se->se_unit ? : "NONE", se->se_desc ? : "NONE");

  return se;
}

int schema_init(struct schema *sc, const char *def)
{
  int rc = -1;
  size_t nr_se = 0;
  size_t i = 0;
  char *cpy = strdup(def), *str = cpy, *tok;

  if (dict_init(&sc->sc_dict, 0) < 0) {
    ERROR("cannot initialize schema: %m\n");
    goto err;
  }

  while ((tok = wsep(&str)) != NULL) {
    struct schema_entry *se = parse_schema_entry(tok);
    if (se == NULL)
      goto err;

    se->se_index = nr_se++;
    if (dict_set(&sc->sc_dict, se->se_key) < 0)
      goto err;
  }

  sc->sc_len = nr_se;
  sc->sc_ent = (struct schema_entry **) calloc(sc->sc_len, sizeof(*sc->sc_ent));
  if (sc->sc_ent == NULL && sc->sc_len != 0) {
    ERROR("cannot allocate schema entries: %m\n");
    goto err;
  }

  char *key;
  while ((key = dict_for_each(&sc->sc_dict, &i)) != NULL) {
    struct schema_entry *se = key_to_schema_entry(key);
    sc->sc_ent[se->se_index] = se;
    TRACE("i %zu, d_key `%s', se_key `%s', se_index %u\n", i, key, se->se_key, se->se_index);
  }

  rc = 0;
 err:
  free(cpy);
  return rc;
}

void schema_destroy(struct schema *sc)
{
  size_t i;
  for (i = 0; i < sc->sc_len; i++) {
    struct schema_entry *se = sc->sc_ent[i];
    free(se->se_unit);
    free(se->se_desc);
    free(se);
  }
  free(sc->sc_ent);
  dict_destroy(&sc->sc_dict, NULL);
  memset(sc, 0, sizeof(struct schema));
}
