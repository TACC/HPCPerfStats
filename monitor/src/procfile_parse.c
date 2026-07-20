/* Line-at-a-time iteration over proc/sys text files. */
#include "procfile_parse.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fileio.h"
#include "path_open_fail_once.h"

#define PROCFILE_IO_BUF 4096

int procfile_for_each_line_skip(const char *path, size_t skip, procfile_line_fn cb, void *ctx)
{
  FILE *file;
  char io_buf[PROCFILE_IO_BUF];
  char *line = NULL;
  size_t line_size = 0;
  size_t skipped = 0;

  if (path == NULL)
    return -1;

  file = path_file_fopen_read(path);
  if (file == NULL)
    return -1;
  setvbuf(file, io_buf, _IOFBF, sizeof(io_buf));

  while (getline(&line, &line_size, file) >= 0) {
    size_t n;

    if (skipped < skip) {
      skipped++;
      continue;
    }

    n = strlen(line);
    if (n > 0 && line[n - 1] == '\n')
      line[n - 1] = '\0';

    if (cb != NULL && cb(line, ctx) != 0)
      break;
  }

  free(line);
  fclose(file);
  return 0;
}

int procfile_for_each_line(const char *path, procfile_line_fn cb, void *ctx)
{
  return procfile_for_each_line_skip(path, 0, cb, ctx);
}
