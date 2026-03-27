#ifndef FILEIO_H
#define FILEIO_H

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>

#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif

/* Avoid fopen(3) on paths: open(2) with O_CLOEXEC, then fdopen for stdio parsing. */
static inline FILE *file_fopen_read(const char *path)
{
  int fd = open(path, O_RDONLY | O_CLOEXEC);
  FILE *fp;

  if (fd < 0)
    return NULL;
  fp = fdopen(fd, "r");
  if (fp == NULL) {
    int saverr = errno;

    close(fd);
    errno = saverr;
  }
  return fp;
}

static inline FILE *file_fopen_append(const char *path)
{
  int fd = open(path, O_RDWR | O_CREAT | O_APPEND, 0666);
  FILE *fp;

  if (fd < 0)
    return NULL;
  fp = fdopen(fd, "a+");
  if (fp == NULL) {
    int saverr = errno;

    close(fd);
    errno = saverr;
  }
  return fp;
}

#endif
