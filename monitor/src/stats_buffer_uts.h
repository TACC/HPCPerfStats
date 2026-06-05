#ifndef STATS_BUFFER_UTS_H_
#define STATS_BUFFER_UTS_H_
#include <sys/utsname.h>

void stats_buffer_ensure_uts_cached(void);
void stats_buffer_uts_cache_reset(void);
const struct utsname *stats_buffer_cached_uts(void);

#endif
