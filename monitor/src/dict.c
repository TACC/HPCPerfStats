#include <stddef.h>
#include <string.h>
#include <malloc.h>
#include <errno.h>
#include "trace.h"
#include "dict.h"

#define DICT_HASH_DUMMY (((hash_t) 1) << (8 * sizeof(hash_t) - 1))
#define DICT_TABLE_LEN_MIN 8
#define DICT_TABLE_LEN_MAX (((size_t) 1) << (8 * sizeof(size_t) - 1))
#define PERTURB_SHIFT 5

/* Stolen from Python's stringobject.c.  GPL. */
// static long
// string_hash(PyStringObject *a)
// {
//   register Py_ssize_t len;
//   register unsigned char *p;
//   register long x;
//
//   if (a->ob_shash != -1)
//     return a->ob_shash;
//   len = Py_SIZE(a);
//   p = (unsigned char *) a->ob_sval;
//   x = *p << 7;
//   while (--len >= 0)
//     x = (1000003*x) ^ *p++;
//   x ^= Py_SIZE(a);
//   if (x == -1)
//     x = -2;
//   a->ob_shash = x;
//   return x;
// }

/* dict_strhash() will never return DICT_HASH_DUMMY. */
hash_t dict_strhash(const char *s)
{
  const unsigned char *p = (const unsigned char *) s;
  hash_t x = *p << 7;

  for (; *p != 0; p++)
    x = (1000003 * x) ^ *p;

  x ^= p - (const unsigned char *) s;

  return x & ~DICT_HASH_DUMMY;
}

int dict_init(struct dict *dict, size_t count)
{
  size_t table_len = DICT_TABLE_LEN_MIN;

  /* Need count < 2/3 of table_size. */
  while (3 * count >= 2 * table_len && table_len < DICT_TABLE_LEN_MAX)
    table_len *= 2;

  memset(dict, 0, sizeof(struct dict));
  dict->d_table = (struct dict_entry *) calloc(table_len, sizeof(struct dict_entry));
  if (dict->d_table == NULL)
    return -1;

  dict->d_table_len = table_len;
  return 0;
}

void dict_destroy(struct dict *dict, void (*key_dtor)(void*))
{
  if (key_dtor != NULL) {
    size_t i;
    for (i = 0; i < dict->d_table_len; i++)
      if (dict->d_table[i].d_key != NULL)
        (*key_dtor)(dict->d_table[i].d_key);
  }
  free(dict->d_table);
  memset(dict, 0, sizeof(struct dict));
}

/* new_table_len must be a power of two. */
static int dict_resize(struct dict *dict, size_t new_table_len)
{
  TRACE("table_len %zu, load %zu, count %zu, new_table_len %zu\n",
        dict->d_table_len, dict->d_load, dict->d_count, new_table_len);

  struct dict_entry *table, *old_table;
  size_t mask, old_table_len;

  table = (struct dict_entry *) calloc(new_table_len, sizeof(struct dict_entry));
  if (table == NULL)
    return -1;

  old_table = dict->d_table;
  old_table_len = dict->d_table_len;

  dict->d_table = table;
  dict->d_table_len = new_table_len;
  dict->d_load = dict->d_count;
  mask = dict->d_table_len - 1;

  size_t i, j;
  for (j = 0; j < old_table_len; j++) {
    hash_t hash = old_table[j].d_hash;
    char *key = old_table[j].d_key;

    /* Do we need to check hash here? */
    if (key == NULL || (hash & DICT_HASH_DUMMY))
      continue;

    size_t perturb = hash;
    i = hash & mask;

    while (table[i & mask].d_key != NULL) {
      i = (i << 2) + i + perturb + 1;
      perturb >>= PERTURB_SHIFT;
    }

    table[i & mask].d_hash = hash;
    table[i & mask].d_key = key;
  }

  free(old_table);

  return 0;
}

void dict_shrink(struct dict *dict, size_t hint)
{
  /* TODO */

  if (dict->d_count == 0 && dict->d_load > dict->d_table_len / 3) {
    memset(dict->d_table, 0, dict->d_table_len * sizeof(struct dict_entry));
    dict->d_load = 0;
  }
}

struct dict_entry *dict_entry_ref(struct dict *dict, hash_t hash, const char *key)
{
  size_t mask, i, perturb;
  struct dict_entry *table, *dummy, *ent;

  mask = dict->d_table_len - 1;
  table = dict->d_table;
  dummy = NULL;

  i = hash & mask;
  ent = &table[i];

  /* TODO Check for ent->d_hash == hash first. */
  if (ent->d_hash & DICT_HASH_DUMMY)
    dummy = ent;
  else if (ent->d_key == NULL)
    return ent;
  else if (ent->d_hash == hash && strcmp(ent->d_key, key) == 0)
    return ent;

  perturb = hash;
  while (1) {
    i = (i << 2) + i + perturb + 1;
    ent = &table[i & mask];

    if (ent->d_hash & DICT_HASH_DUMMY) {
      if (dummy == NULL)
        dummy = ent;
    } else if (ent->d_key == NULL) {
      return (dummy != NULL) ? dummy : ent;
    } else if (ent->d_hash == hash && strcmp(ent->d_key, key) == 0) {
      return ent;
    }

    perturb >>= PERTURB_SHIFT;
  }
}

int dict_entry_set(struct dict *dict, struct dict_entry *ent, hash_t hash, char *key)
{
  size_t new_load = 0;
  /* If we're overwriting an existing entry then we don't need to
     resize. */
  if (ent->d_key != NULL)
    goto out_exist;

  /* Overwriting a dummy entry doesn't affect the load, so we don't
     need to resize. */
  if (ent->d_hash & DICT_HASH_DUMMY)
    goto out_dummy;

  new_load = dict->d_load + 1;
  if (3 * new_load >= 2 * dict->d_table_len) {
    size_t new_count = dict->d_count + 1;
    size_t new_table_len = dict->d_table_len;
    while (3 * new_count >= 2 * new_table_len && new_table_len < DICT_TABLE_LEN_MAX)
      new_table_len *= 2;

    if (new_count >= new_table_len) {
      TRACE("new_count %zu >= new_table_len %zu\n", new_count, new_table_len);
      errno = ENOMEM;
      return -1;
    }

    if (dict_resize(dict, new_table_len) < 0)
      return -1;

    /* Revalidate ent after resize. */
    ent = dict_entry_ref(dict, hash, key);
  }

  dict->d_load++;
 out_dummy:
  dict->d_count++;
 out_exist:
  ent->d_hash = hash;
  ent->d_key = key;

  return 0;
}

char *dict_entry_remv(struct dict *dict, struct dict_entry *ent, int may_resize)
{
  char *key = ent->d_key;
  if (key != NULL) {
    ent->d_hash = DICT_HASH_DUMMY;
    ent->d_key = NULL;
    dict->d_count--;
    if (may_resize)
      dict_shrink(dict, dict->d_count);
  }

  return key;
}

char *dict_remv(struct dict *dict, const char *key)
{
  hash_t hash = dict_strhash(key);
  struct dict_entry *ent = dict_entry_ref(dict, hash, key);

  return dict_entry_remv(dict, ent, 1);
}

char *dict_ref(struct dict *dict, const char *key)
{
  hash_t hash = dict_strhash(key);
  struct dict_entry *ent = dict_entry_ref(dict, hash, key);
  return ent->d_key;
}

int dict_set(struct dict *dict, char *key)
{
  hash_t hash = dict_strhash(key);
  struct dict_entry *ent = dict_entry_ref(dict, hash, key);

  if (ent->d_key != NULL) {
    TRACE("overwriting old key `%s', hash %zu, with new key `%s' hash %zu\n",
          ent->d_key, ent->d_hash, key, hash);
    ent->d_key = key;
    return 0;
  }

  if (dict_entry_set(dict, ent, hash, key) < 0)
    return -1;

  return 0;
}

struct dict_entry *dict_for_each_ref(struct dict *dict, size_t *i)
{
  while (*i < dict->d_table_len) {
    struct dict_entry *ent = dict->d_table + (*i)++;
    if (ent->d_key != NULL)
      return ent;
  }

  return NULL;
}

char *dict_for_each(struct dict *dict, size_t *i)
{
  struct dict_entry *ent = dict_for_each_ref(dict, i);
  if (ent != NULL)
    return ent->d_key;
  return NULL;
}
