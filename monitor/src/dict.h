#ifndef _DICT_H_
#define _DICT_H_
#include <stddef.h>

typedef unsigned long hash_t;
hash_t dict_strhash(const char *s);

struct dict_entry {
  hash_t d_hash;
  char *d_key;
};

struct dict {
  struct dict_entry *d_table;
  size_t d_table_len;
  size_t d_load;
  size_t d_count;
};

/* The count argument is only a hint. */
int dict_init(struct dict *dict, size_t count);

/* Valid for zero-initialized dicts or ones from dict_init(). Frees keys only via key_dtor. */
void dict_destroy(struct dict *dict, void (*key_dtor)(void *));

struct dict_entry *dict_entry_ref(struct dict *dict, hash_t hash, const char *key);
int dict_entry_set(struct dict *dict, struct dict_entry *ent, hash_t hash, char *key);
char *dict_entry_remv(struct dict *dict, struct dict_entry *ent, int may_resize);

char *dict_ref(struct dict *dict, const char *key);
int dict_set(struct dict *dict, char *key);
char *dict_remv(struct dict *dict, const char *key);

/* Returns only non-NULL keys. */
char *dict_for_each(struct dict *dict, size_t *i);
struct dict_entry *dict_for_each_ref(struct dict *dict, size_t *i);

#endif
