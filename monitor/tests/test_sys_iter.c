#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include "sys_iter.h"

struct visit_log {
  size_t n;
  char names[16][64];
};

static void visit_cb(const char *base, const char *name, void *ctx)
{
  struct visit_log *vl = (struct visit_log *)ctx;

  (void)base;
  if (vl->n >= sizeof(vl->names) / sizeof(vl->names[0]))
    return;
  snprintf(vl->names[vl->n], sizeof(vl->names[vl->n]), "%s", name);
  vl->n++;
}

static int by_name(const void *a, const void *b)
{
  return strcmp((const char *)a, (const char *)b);
}

static void make_file(const char *dir, const char *name)
{
  char path[512];
  int fd;

  snprintf(path, sizeof(path), "%s/%s", dir, name);
  fd = open(path, O_CREAT | O_WRONLY | O_TRUNC, 0600);
  assert(fd >= 0);
  close(fd);
}

int main(void)
{
  char tmpl[] = "/tmp/hps_sys_iterXXXXXX";
  char *root = mkdtemp(tmpl);

  assert(root != NULL);

  make_file(root, "alpha");
  make_file(root, "beta");
  make_file(root, ".dotfile");
  make_file(root, "gamma");

  struct visit_log vl = { 0 };
  int rc = sys_iter_for_each(root, visit_cb, &vl);

  assert(rc == 0);
  assert(vl.n == 3);

  qsort(vl.names, vl.n, sizeof(vl.names[0]), by_name);
  assert(strcmp(vl.names[0], "alpha") == 0);
  assert(strcmp(vl.names[1], "beta") == 0);
  assert(strcmp(vl.names[2], "gamma") == 0);

  /* cleanup */
  char path[512];

  snprintf(path, sizeof(path), "%s/alpha", root);  unlink(path);
  snprintf(path, sizeof(path), "%s/beta", root);   unlink(path);
  snprintf(path, sizeof(path), "%s/.dotfile", root); unlink(path);
  snprintf(path, sizeof(path), "%s/gamma", root);  unlink(path);
  rmdir(root);

  /* Missing directory: returns -1, callback never invoked. */
  vl.n = 0;
  rc = sys_iter_for_each("/nonexistent/hps_sys_iter_xx", visit_cb, &vl);
  assert(rc == -1);
  assert(vl.n == 0);

  /* NULL callback is a no-op (just exercises the path). */
  char tmpl2[] = "/tmp/hps_sys_iterXXXXXX";
  root = mkdtemp(tmpl2);
  assert(root != NULL);
  make_file(root, "x");
  rc = sys_iter_for_each(root, NULL, NULL);
  assert(rc == 0);
  snprintf(path, sizeof(path), "%s/x", root); unlink(path);
  rmdir(root);

  puts("test_sys_iter passed");
  return 0;
}
