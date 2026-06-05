/* lustre_obd_to_mnt — map Lustre OBD names to mount points via /proc/fs/lustre/lov. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <dirent.h>
#include <mntent.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include "stats.h"
#include "trace.h"
#include "dict.h"
#include "string1.h"
#include "lustre_obd_to_mnt.h"
#include "path_open_fail_once.h"

#define LUSTRE_SB_HEX_LEN 16
#define LUSTRE_SB_MNT_OFFSET (LUSTRE_SB_HEX_LEN + 1)

struct dict sb_dict;

static int lustre_lov_name_valid(const char *lov_name)
{
  if (lov_name == NULL)
    return 0;
  return strlen(lov_name) >= LUSTRE_SB_HEX_LEN;
}

static char *lustre_sb_mnt_alloc(const char *sb_hex, const char *mnt_prefix)
{
  size_t need;
  char *sb_mnt;

  if (sb_hex == NULL || mnt_prefix == NULL)
    return NULL;
  need = LUSTRE_SB_MNT_OFFSET + strlen(mnt_prefix) + 1;
  sb_mnt = malloc(need);
  if (sb_mnt == NULL)
    return NULL;
  if (snprintf(sb_mnt, LUSTRE_SB_MNT_OFFSET, "%s", sb_hex) >= (int) LUSTRE_SB_MNT_OFFSET) {
    free(sb_mnt);
    return NULL;
  }
  if (snprintf(sb_mnt + LUSTRE_SB_MNT_OFFSET, need - LUSTRE_SB_MNT_OFFSET, "%s",
               mnt_prefix) >= (int) (need - LUSTRE_SB_MNT_OFFSET)) {
    free(sb_mnt);
    return NULL;
  }
  return sb_mnt;
}

static int lustre_sb_dict_add_lov(const char *lov_name)
{
  char lov_copy[128];
  const char *sb;
  hash_t hash;
  struct dict_entry *de;
  char *sb_mnt;

  if (!lustre_lov_name_valid(lov_name))
    return -1;

  if (snprintf(lov_copy, sizeof(lov_copy), "%s", lov_name) >= (int) sizeof(lov_copy))
    return -1;

  sb = lov_copy + strlen(lov_copy) - LUSTRE_SB_HEX_LEN;
  hash = dict_strhash(sb);
  de = dict_entry_ref(&sb_dict, hash, sb);
  if (de->d_key != NULL) {
    TRACE("multiple filesystems with super block `%s'\n", sb);
    return 0;
  }

  lov_copy[strlen(lov_copy) - LUSTRE_SB_HEX_LEN - 1] = '\0';
  sb_mnt = lustre_sb_mnt_alloc(sb, lov_copy);
  if (sb_mnt == NULL) {
    ERROR("cannot allocate sb_mnt: %m\n");
    return -1;
  }

  if (dict_entry_set(&sb_dict, de, hash, sb_mnt) < 0) {
    ERROR("cannot set sb_dict entry: %m\n");
    free(sb_mnt);
    return -1;
  }
  return 0;
}

static void lustre_sb_dict_load_lov_dir(DIR *lov_dir)
{
  struct dirent *de;

  if (lov_dir == NULL)
    return;

  while ((de = readdir(lov_dir)) != NULL) {
    if (de->d_type != DT_DIR || de->d_name[0] == '.')
      continue;
    if (!lustre_lov_name_valid(de->d_name)) {
      ERROR("invalid lov name `%s'\n", de->d_name);
      continue;
    }
    (void) lustre_sb_dict_add_lov(de->d_name);
  }
}

__attribute__((constructor))
static void sb_dict_init(void)
{
  const char *lov_dir_path = "/proc/fs/lustre/lov";
  DIR *lov_dir = NULL;

  if (dict_init(&sb_dict, 8) < 0) {
    ERROR("cannot create sb_dict: %m\n");
    return;
  }

  lov_dir = path_opendir_or_record_fail(lov_dir_path);
  if (lov_dir == NULL)
    goto out;

  lustre_sb_dict_load_lov_dir(lov_dir);

 out:
  if (lov_dir != NULL)
    closedir(lov_dir);

  TRACE("found %zu lustre filesystems\n", sb_dict.d_count);

#ifdef DEBUG
  do {
    size_t i = 0;
    char *entry;

    while ((entry = dict_for_each(&sb_dict, &i)) != NULL)
      TRACE("sb `%s', mnt `%s'\n", entry, entry + LUSTRE_SB_MNT_OFFSET);
  } while (0);
#endif
}

char *lustre_obd_to_mnt(const char *name)
{
  char *sb_mnt;

  if (name == NULL)
    return NULL;

  if (!lustre_lov_name_valid(name)) {
    char inv[192];

    snprintf(inv, sizeof(inv), "lustre:inv:%s", name);
    if (!path_open_is_skipped(inv)) {
      ERROR("invalid obd name `%s'\n", name);
      path_fail_mark(inv);
    }
    return NULL;
  }

  sb_mnt = dict_ref(&sb_dict, name + strlen(name) - LUSTRE_SB_HEX_LEN);
  if (sb_mnt == NULL) {
    char miss[384];

    snprintf(miss, sizeof(miss), "lustre:sb:%s", name);
    if (!path_open_is_skipped(miss)) {
      ERROR("no super block found for obd `%s'. build a new super block dict\n", name);
      path_fail_mark(miss);
    }
    sb_dict_init();
    sb_mnt = dict_ref(&sb_dict, name + strlen(name) - LUSTRE_SB_HEX_LEN);
  }

  if (sb_mnt == NULL)
    return NULL;

  return sb_mnt + LUSTRE_SB_MNT_OFFSET;
}
