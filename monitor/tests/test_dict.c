#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dict.h"

static void test_strhash(void)
{
  hash_t h_foo1;
  hash_t h_foo2;
  hash_t h_bar;

  assert(dict_strhash(NULL) == 0);
  h_foo1 = dict_strhash("foo");
  h_foo2 = dict_strhash("foo");
  h_bar = dict_strhash("bar");
  assert(h_foo1 == h_foo2);
  assert(h_foo1 != h_bar);
}

static void test_init_and_null_args(void)
{
  struct dict d;

  assert(dict_init(NULL, 0) == -1);
  assert(dict_init(&d, 0) == 0);
  assert(d.d_table != NULL);
  assert(d.d_table_len >= 8);
  dict_destroy(&d, NULL);

  assert(dict_ref(NULL, "key") == NULL);
  assert(dict_set(NULL, (char *)"key") == -1);
  assert(dict_init(&d, 2) == 0);
  assert(dict_ref(&d, NULL) == NULL);
  assert(dict_set(&d, NULL) == -1);
  dict_destroy(&d, NULL);
}

static void test_set_ref_remv(void)
{
  struct dict d;
  char *k_alpha;
  char *k_beta;
  char *removed;

  k_alpha = strdup("alpha");
  k_beta = strdup("beta");
  assert(k_alpha != NULL && k_beta != NULL);

  assert(dict_init(&d, 2) == 0);
  assert(dict_set(&d, k_alpha) == 0);
  assert(dict_set(&d, k_beta) == 0);
  assert(dict_ref(&d, "alpha") == k_alpha);
  assert(dict_ref(&d, "beta") == k_beta);
  assert(dict_ref(&d, "missing") == NULL);

  removed = dict_remv(&d, "alpha");
  assert(removed == k_alpha);
  assert(dict_ref(&d, "alpha") == NULL);
  assert(dict_ref(&d, "beta") == k_beta);

  free(removed);
  free(k_beta);
  dict_destroy(&d, NULL);
}

static void test_for_each(void)
{
  struct dict d;
  char *keys[3];
  size_t i = 0;
  int count = 0;

  keys[0] = strdup("a");
  keys[1] = strdup("b");
  keys[2] = strdup("c");
  assert(keys[0] != NULL && keys[1] != NULL && keys[2] != NULL);

  assert(dict_init(&d, 4) == 0);
  assert(dict_set(&d, keys[0]) == 0);
  assert(dict_set(&d, keys[1]) == 0);
  assert(dict_set(&d, keys[2]) == 0);

  while (dict_for_each(&d, &i) != NULL)
    count++;
  assert(count == 3);

  dict_destroy(&d, free);
}

static void test_resize_on_many_inserts(void)
{
  struct dict d;
  size_t i;

  assert(dict_init(&d, 1) == 0);
  for (i = 0; i < 32; i++) {
    char buf[16];
    char *key;

    snprintf(buf, sizeof(buf), "k%zu", i);
    key = strdup(buf);
    assert(key != NULL);
    assert(dict_set(&d, key) == 0);
    assert(dict_ref(&d, buf) == key);
  }
  assert(d.d_count == 32);
  dict_destroy(&d, free);
}

/* ICX host_block GPF: dict_entry_ref must not SEGV on null/zero/corrupt tables. */
static void test_entry_ref_null_and_zero_table(void)
{
  struct dict d;
  hash_t h;

  memset(&d, 0, sizeof(d));
  h = dict_strhash("sda");
  assert(dict_entry_ref(NULL, h, "sda") == NULL);
  assert(dict_entry_ref(&d, h, "sda") == NULL); /* d_table NULL */

  assert(dict_init(&d, 0) == 0);
  free(d.d_table);
  d.d_table = NULL;
  d.d_table_len = 8;
  assert(dict_entry_ref(&d, h, "sda") == NULL);
  d.d_table_len = 0;
  assert(dict_entry_ref(&d, h, "sda") == NULL);

  assert(dict_init(&d, 0) == 0);
  assert(dict_entry_ref(&d, h, NULL) == NULL);
  assert(dict_ref(&d, NULL) == NULL);
  dict_destroy(&d, NULL);
}

static void test_entry_ref_bounded_corrupt_full_table(void)
{
  struct dict d;
  size_t i;
  hash_t want;

  assert(dict_init(&d, 0) == 0);
  want = dict_strhash("probe-miss");
  for (i = 0; i < d.d_table_len; i++) {
    d.d_table[i].d_key = (char *)"fill";
    d.d_table[i].d_hash = want ^ (hash_t)(i + 1);
  }
  /* No empty slot and no matching key — must return NULL, not hang/SEGV. */
  assert(dict_entry_ref(&d, want, "probe-miss") == NULL);
  assert(dict_ref(&d, "probe-miss") == NULL);
  dict_destroy(&d, NULL);
}

int main(void)
{
  test_strhash();
  test_init_and_null_args();
  test_set_ref_remv();
  test_for_each();
  test_resize_on_many_inserts();
  test_entry_ref_null_and_zero_table();
  test_entry_ref_bounded_corrupt_full_table();
  printf("test_dict passed\n");
  return 0;
}
