#include <malloc.h>
#include <stdlib.h>
#include <string.h>
#include "stats.h"
#include "dict.h"
#include "trace.h"
#include "schema.h"
#include "string1.h"

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
