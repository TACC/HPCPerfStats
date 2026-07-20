#ifndef HOST_MEM_H_
#define HOST_MEM_H_

#include "stats.h"

#define KEYS                                                                                       \
  X(mem_total, "U=KB", ""), X(mem_free, "U=KB", ""), X(mem_used, "U=KB", ""),                      \
      X(active, "U=KB", ""), X(inactive, "U=KB", ""), X(dirty, "U=KB", ""),                        \
      X(writeback, "U=KB", ""), X(file_pages, "U=KB", ""), X(mapped, "U=KB", ""),                  \
      X(anon_pages, "U=KB", ""), X(page_tables, "U=KB", ""), X(nfs_unstable, "U=KB", ""),          \
      X(bounce, "U=KB", ""), X(slab, "U=KB", ""), X(anon_huge_pages, "U=KB", ""),                  \
      X(huge_pages_total, "", ""), X(huge_pages_free, "", "")

#endif
