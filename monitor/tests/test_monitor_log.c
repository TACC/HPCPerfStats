#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "monitor_log.h"

int main(void)
{
  FILE *f = tmpfile();

  assert(f != NULL);
  monitor_log_set_stream(f);
  monitor_log_info("n=%d txt=%s\n", 7, "ok");

  rewind(f);
  char buf[128];
  size_t n = fread(buf, 1, sizeof(buf) - 1u, f);
  buf[n] = '\0';
  assert(strcmp(buf, "n=7 txt=ok\n") == 0);

  monitor_log_set_stream(NULL);
  assert(monitor_log_get_stream() == stderr);

  fclose(f);
  return 0;
}
