#!/bin/sh
# Automake LOG_COMPILER for foreign-arch make check: run a compiled test under the emulator
# given as $1 and a script test (#! first line) natively.
# usage: LOG_COMPILER='sh .../log_compiler_exec.sh qemu-aarch64-static'
emu="$1"
shift
case "$(head -c 2 "$1" 2>/dev/null)" in
'#!') exec "$@" ;;
*) exec "${emu}" "$@" ;;
esac
