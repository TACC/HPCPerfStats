#!/bin/bash
# Alpine: compiler-rt provides libclang_rt.profile-x86_64.a; -fprofile-instr-generate links libclang_rt.profile.a.
set -euo pipefail
test -x /usr/bin/clang
test -x /usr/bin/ld.lld
_rt=/usr/lib/llvm22/lib/clang/22/lib
_triple="$(clang -dumpmachine)"
_prof_dir="${_rt}/${_triple}"
_prof_x86="${_prof_dir}/libclang_rt.profile-x86_64.a"
_prof_link="${_prof_dir}/libclang_rt.profile.a"
test -f "${_prof_x86}"
ln -sf libclang_rt.profile-x86_64.a "${_prof_link}"
test -f "${_prof_link}"
