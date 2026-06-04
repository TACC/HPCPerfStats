#ifndef HOST_KEY_ALIAS_H_
#define HOST_KEY_ALIAS_H_

#include "stats.h"

/* Map kernel meminfo / proc status field names to emitted snake_case keys. */
const char *host_key_alias_lookup(const char *kernel_key);
void host_key_alias_emit(struct stats *stats, const char *kernel_key,
                         unsigned long long val);

#endif
