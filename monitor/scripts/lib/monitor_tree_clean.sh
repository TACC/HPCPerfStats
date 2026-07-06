#!/usr/bin/env bash
# Shared cleanup for monitor Autotools / build-script workspaces.
# Sourced by prepare_rpmbuild_dirs.sh and build_static_bundle.sh (see monitor-build-clean-workspace).
#
# monitor_tree_clean_pre_dist  — before autoreconf/configure/compile in MONITOR_DIR
# monitor_tree_clean_post_dist — after make dist + tarball copy (prepare only)

monitor_tree_clean_pre_dist() {
  local monitor_dir="${1:?monitor_dir required}"
  local tarball="${2:-}"

  if test -f "${monitor_dir}/Makefile"; then
    echo "Running make distclean in ${monitor_dir} ..."
    (cd "${monitor_dir}" && make distclean) || true
  fi

  if test -d "${monitor_dir}/.build-static"; then
    echo "Removing ${monitor_dir}/.build-static ..."
    rm -rf "${monitor_dir}/.build-static"
  fi

  # Other .build* trees (e.g. legacy names); skip rpmbuild under monitor/.
  local build_tree
  for build_tree in "${monitor_dir}"/.build*; do
    test -e "${build_tree}" || continue
    case "${build_tree}" in
      */.build-static) continue ;;
    esac
    echo "Removing ${build_tree} ..."
    rm -rf "${build_tree}"
  done

  if test -d "${monitor_dir}/autom4te.cache"; then
    echo "Removing ${monitor_dir}/autom4te.cache ..."
    rm -rf "${monitor_dir}/autom4te.cache"
  fi

  if test -n "${tarball}"; then
    rm -f "${monitor_dir}/${tarball}"
  fi
  rm -f "${monitor_dir}"/*.tar.gz
}

monitor_tree_clean_post_dist() {
  local monitor_dir="${1:?monitor_dir required}"
  local tarball="${2:-}"

  if test -f "${monitor_dir}/Makefile"; then
    echo "Running make distclean in ${monitor_dir} (post-dist) ..."
    (cd "${monitor_dir}" && make distclean) || true
  fi

  if test -n "${tarball}"; then
    rm -f "${monitor_dir}/${tarball}"
  fi

  if test -d "${monitor_dir}/autom4te.cache"; then
    rm -rf "${monitor_dir}/autom4te.cache"
  fi
}

monitor_tree_clean_build_static() {
  local monitor_dir="${1:?monitor_dir required}"

  if test "${SKIP_CLEAN:-0}" = "1"; then
    return 0
  fi

  if test -d "${monitor_dir}/.build-static"; then
    echo "Removing prior monitor build tree (failed or stale): ${monitor_dir}/.build-static"
    rm -rf "${monitor_dir}/.build-static"
  fi
}

verify_dist_tarball_host_headers() {
  local tarball="${1:?tarball required}"
  local tarbase="${2:?tarbase required}"
  local missing=0
  local hdr

  for hdr in host_cpu.h host_mem.h host_net.h host_ps.h; do
    if ! tar tzf "${tarball}" "${tarbase}/src/${hdr}" >/dev/null 2>&1; then
      echo "Source tarball missing ${tarbase}/src/${hdr}" >&2
      missing=1
    fi
  done

  if test "${missing}" -ne 0; then
    echo "Rebuild after adding collector host_*.h to hpcperfstatsd_SOURCES in src/Makefile.am." >&2
    return 1
  fi
  return 0
}
