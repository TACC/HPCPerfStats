#ifndef IB_PORT_STATE_H_
#define IB_PORT_STATE_H_

/* Parse InfiniBand port state/phys_state sysfs line text (pure logic). */
int ib_port_logic_active(const char *state_line);
int ib_port_phys_link_up(const char *phys_line);

#endif
