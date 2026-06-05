#ifndef _IB_FAMILY_H_
#define _IB_FAMILY_H_

struct stats_type;

void ib_family_collect(struct stats_type *type);
void ib_family_disable_all(void);

#endif
