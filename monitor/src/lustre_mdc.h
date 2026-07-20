#ifndef LUSTRE_MDC_H_
#define LUSTRE_MDC_H_

#include "stats.h"

#define KEYS \
  X(ldlm_cancel, "E", ""), \
  X(mds_close, "E", ""), \
  X(mds_getattr, "E", ""), \
  X(mds_getattr_lock, "E", ""), \
  X(mds_getxattr, "E", ""), \
  X(mds_readpage, "E", ""), \
  X(mds_statfs, "E", ""), \
  X(mds_sync, "E", ""), \
  X(reqs, "E", ""), \
  X(wait, "E,U=us", "")

#endif
