/* Recursive removal of a test fixture tree without a shell. Fixture roots come
 * from $TMPDIR, so "rm -rf '<root>'" through system() ran whatever a quote in
 * TMPDIR injected. nftw walks depth-first and never follows symlinks. */
#ifndef TEST_RM_TREE_H
#define TEST_RM_TREE_H

#include <ftw.h>
#include <stdio.h>
#include <sys/stat.h>

static inline int test_rm_tree_entry(const char *path, const struct stat *sb, int flag,
                                     struct FTW *ftw)
{
  (void)sb;
  (void)flag;
  (void)ftw;
  return remove(path);
}

/* Returns 0 when root and everything under it is gone, -1 otherwise. */
static inline int test_rm_tree(const char *root)
{
  return nftw(root, test_rm_tree_entry, 16, FTW_DEPTH | FTW_PHYS);
}

#endif
