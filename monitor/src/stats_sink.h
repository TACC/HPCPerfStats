#ifndef STATS_SINK_H
#define STATS_SINK_H

/*! Optional finalize hook after all enabled stats types are collected
 *  (e.g. assemble payload into a stats_buffer).
 */
struct stats_sink_ops {
  int (*finalize)(void *opaque);
};

#endif
