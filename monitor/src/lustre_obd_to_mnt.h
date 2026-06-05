#ifndef _LUSTRE_OBD_TO_MNT_H_
#define _LUSTRE_OBD_TO_MNT_H_

/* Resolve Lustre OBD device name (…-ffff…) to mount point; NULL if unknown. */
char *lustre_obd_to_mnt(const char *name);

#endif
