#!/bin/sh
# Site override file for the RPM conf, and the fleet broker guard.
#
#   site_conf.sh overlay DEFAULT SITE OUT
#       Write DEFAULT with SITE's keys laid over it to OUT (mode 0600; OUT may be DEFAULT).
#       A key present in DEFAULT has its value replaced in place (every occurrence); a key
#       absent from DEFAULT is appended; comments, blank lines and order of DEFAULT are kept.
#       No SITE file: OUT is a byte copy of DEFAULT. Run by hpcperfstats.spec %build.
#
#   site_conf.sh fleet-guard MONITOR_DIR
#       When a fleet is selected (HPCS_BUNDLE_FLEET, or any MONITOR_DIR/scripts/fleet/*.force),
#       exit 1 unless the effective broker server (src/hpcperfstats.conf + the site file) is a
#       real host: empty, localhost and loopback addresses are refused. HPCS_ALLOW_DEFAULT_BROKER=1
#       downgrades the refusal to a warning. Non-fleet builds always pass. Run by
#       prepare_rpmbuild_dirs.sh before any build step.
#
# Line syntax matches the daemon's read_conf_file(): the key ends at the first space, tab, ':'
# or '='; the value is the trimmed rest; '#' lines are comments. Values are never printed
# (the site file holds broker credentials); messages name keys and paths only.
set -eu

readonly SITE_REL="site/hpcperfstats.site.conf"

die() {
  echo "site_conf: ERROR: $*" >&2
  exit 1
}

# conf_awk DEFAULT SITE [GET_KEY]: without GET_KEY print the overlay; with it print only that
# key's effective value. SITE may be "" (no overrides).
conf_awk() {
  awk -v site="$2" -v get="${3:-}" '
    function trim(s) { sub(/^[ \t\r]+/, "", s); sub(/[ \t\r]+$/, "", s); return s }
    function parse(line,   t, p) {
      t = trim(line); pk = ""; pv = ""
      if (t == "" || substr(t, 1, 1) == "#") return 0
      p = match(t, /[ \t:=]/)
      if (p == 0) { pk = t; return 1 }
      pk = substr(t, 1, p - 1); pv = trim(substr(t, p + 1)); return 1
    }
    function kv(k) { return val[k] == "" ? k : k " " val[k] }
    BEGIN {
      n = 0
      if (site != "") {
        while ((r = (getline line < site)) > 0) {
          if (!parse(line)) continue
          if (!(pk in val)) order[++n] = pk
          val[pk] = pv
        }
        if (r < 0) { print "site_conf: ERROR: cannot read " site > "/dev/stderr"; exit 2 }
        close(site)
      }
    }
    get != "" { if (parse($0) && pk == get) dflt = pv; next }
    {
      if (parse($0) && (pk in val)) { print kv(pk); used[pk] = 1; next }
      print
    }
    END {
      if (get != "") { print ((get in val) ? val[get] : dflt); exit }
      for (i = 1; i <= n; i++) if (!(order[i] in used)) print kv(order[i])
    }
  ' "$1"
}

site_keys() {
  awk '{ sub(/^[ \t\r]+/, "") } $0 != "" && substr($0, 1, 1) != "#" {
    split($0, f, /[ \t:=]/); if (!(f[1] in seen)) { seen[f[1]] = 1; printf " %s", f[1] } }' "$1"
}

cmd_overlay() {
  test "$#" -eq 3 || die "usage: site_conf.sh overlay DEFAULT SITE OUT"
  default="$1" site="$2" out="$3"
  test -f "${default}" || die "default conf not found: ${default}"
  if test -e "${site}" && ! test -r "${site}"; then
    die "site conf not readable: ${site}"
  fi
  tmp="$(mktemp "$(dirname "${out}")/.site_conf.XXXXXX")"
  chmod 0600 "${tmp}"
  if test -f "${site}"; then
    if ! conf_awk "${default}" "${site}" >"${tmp}"; then
      rm -f "${tmp}"
      die "overlay of ${site} onto ${default} failed"
    fi
    echo "site_conf: ${out}: keys from ${site}:$(site_keys "${site}")"
  else
    cat "${default}" >"${tmp}"
    echo "site_conf: no ${site}; ${out} is the repo default"
  fi
  mv -f "${tmp}" "${out}"
}

cmd_fleet_guard() {
  test "$#" -eq 1 || die "usage: site_conf.sh fleet-guard MONITOR_DIR"
  mon="$1"
  fleet="${HPCS_BUNDLE_FLEET:-}"
  if test -z "${fleet}"; then
    for marker in "${mon}"/scripts/fleet/*.force; do
      if test -f "${marker}"; then
        fleet="$(basename "${marker}" .force)"
        break
      fi
    done
  fi
  test -n "${fleet}" || return 0

  default="${mon}/src/hpcperfstats.conf"
  test -f "${default}" || die "default conf not found: ${default}"
  site=""
  source_desc="the repo default src/hpcperfstats.conf"
  if test -f "${mon}/${SITE_REL}"; then
    site="${mon}/${SITE_REL}"
    source_desc="${SITE_REL}"
  fi
  server="$(conf_awk "${default}" "${site}" server | tr '[:upper:]' '[:lower:]')"
  case "${server}" in
    "" | localhost | localhost.localdomain | 127.* | ::1) ;;
    *)
      echo "site_conf: fleet ${fleet}: broker server set by ${source_desc}"
      return 0
      ;;
  esac
  if test "${HPCS_ALLOW_DEFAULT_BROKER:-0}" = "1"; then
    echo "site_conf: WARNING: fleet ${fleet} RPM will publish to a local/empty broker server" \
      "(HPCS_ALLOW_DEFAULT_BROKER=1)" >&2
    return 0
  fi
  cat >&2 <<EOF
site_conf: ERROR: fleet ${fleet} build refused: the RPM conf would publish to a local or
empty broker server (from ${source_desc}). Every node installing this RPM would stop
sending data. Put the site broker settings in ${mon}/${SITE_REL}
(copy ${SITE_REL}.example; keys: server, queue, port, user, password), or set
HPCS_ALLOW_DEFAULT_BROKER=1 for a deliberate local-broker build.
EOF
  exit 1
}

case "${1:-}" in
  overlay)
    shift
    cmd_overlay "$@"
    ;;
  fleet-guard)
    shift
    cmd_fleet_guard "$@"
    ;;
  *)
    die "usage: site_conf.sh {overlay DEFAULT SITE OUT | fleet-guard MONITOR_DIR}"
    ;;
esac
