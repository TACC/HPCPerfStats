/* Force-included into every tests/ translation unit (tests/Makefile.am AM_CPPFLAGS).
 * Drivers put required calls inside assert() (dict_init, fscanf, ...). A release
 * build adds -DNDEBUG (build_static_bundle.sh), which compiled those calls out:
 * tests read uninitialised state and test_daemonize ran kill(0, SIGTERM) on make.
 * -include is applied after all -D/-U options, so this wins over CFLAGS. */
#undef NDEBUG
