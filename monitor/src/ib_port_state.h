#ifndef IB_PORT_STATE_H_
#define IB_PORT_STATE_H_

/* Parse InfiniBand port state/phys_state/link_layer sysfs line text (pure logic). */
int ib_port_logic_active(const char *state_line);
int ib_port_phys_link_up(const char *phys_line);
/* 1 if link_layer sysfs text is InfiniBand; 0 for Ethernet / empty / NULL / unknown. */
int ib_port_link_layer_is_infiniband(const char *link_layer_line);

#endif
