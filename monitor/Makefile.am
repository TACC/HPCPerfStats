SUBDIRS = src tests

# Monitor-specific trees (not created by Autoconf); removed by distclean.
distclean-local:
	rm -rf .build-static

# Pre-built static libev / rabbitmq-c / (x86) LIKWID from scripts/prepare_rpmbuild_dirs.sh.
# Shipped inside the release tarball for rpmbuild; the spec links the daemon against this tree only.
# make dist requires HPC_BUNDLE_EMBED_PREFIX=1 and a populated embedded-static-prefix/; see prepare script.
dist-hook:
	@test "x$(HPC_BUNDLE_EMBED_PREFIX)" = x1 || { \
	  echo "Set HPC_BUNDLE_EMBED_PREFIX=1 and populate embedded-static-prefix/ (see scripts/prepare_rpmbuild_dirs.sh)." >&2; \
	  exit 1; \
	}
	@test -d "$(srcdir)/embedded-static-prefix/include" \
	  && { test -d "$(srcdir)/embedded-static-prefix/lib" || test -d "$(srcdir)/embedded-static-prefix/lib64"; } \
	  || { echo "embedded-static-prefix/ is missing or incomplete." >&2; exit 1; }
	cp -a "$(srcdir)/embedded-static-prefix" "$(distdir)/embedded-static-prefix"

EXTRA_DIST = README README.md \
	third_party/nvidia-dcgm/README \
	third_party/nvidia-dcgm/dcgm_agent.h \
	third_party/nvidia-dcgm/dcgm_structs.h \
	third_party/nvidia-dcgm/dcgm_fields.h
