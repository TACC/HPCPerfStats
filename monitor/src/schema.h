#ifndef _SCHEMA_H_
#define _SCHEMA_H_
#include <stddef.h>
#include "dict.h"

#define SE_EVENT 1
#define SE_CONTROL 2

struct schema_entry {
  char *se_unit;
  char *se_desc;
  unsigned int se_index;
  unsigned int se_type;
  unsigned int se_width;
  char se_key[];
};

struct schema {
  size_t sc_len;
  struct schema_entry **sc_ent;
  struct dict sc_dict;
};

static inline struct schema_entry *key_to_schema_entry(const char *key)
{
  /* Cannot be replaced with sizeof(*se), (due to alignment) at least with 3.4.6. */
  size_t se_key_offset = (((struct schema_entry *) NULL)->se_key) - (char *) NULL;
  return (struct schema_entry *) (key - se_key_offset);
}

struct schema_entry *parse_schema_entry(char *str);

int schema_init(struct schema *sc, const char *def);
void schema_destroy(struct schema *sc);

static inline int schema_ref(struct schema *sc, const char *key)
{
  char *sk = dict_ref(&sc->sc_dict, key);
  if (sk == NULL)
    return -1;

  return key_to_schema_entry(sk)->se_index;
}

#endif
