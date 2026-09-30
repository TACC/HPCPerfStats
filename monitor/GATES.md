# Gates: F6 privilege hardening

OWNS: HPCPerfStats/monitor/src/hpcperfstats.service, HPCPerfStats/monitor/hpcperfstats.spec, HPCPerfStats/monitor/tests/test_hpcperfstats_service_sandbox.sh, HPCPerfStats/monitor/tests/test_monitor_cli.c

- [ ] G1: Unit sandbox regression script passes
  CHECK: bash tests/test_hpcperfstats_service_sandbox.sh
  EXPECT: test_hpcperfstats_service_sandbox.sh passed
  CWD: HPCPerfStats/monitor

- [ ] G2: Default dump path literal
  CHECK: bash -c 'grep -q "/var/lib/hpcperfstats/dump" src/monitor_cli.c && echo G2_OK'
  EXPECT: G2_OK
  CWD: HPCPerfStats/monitor

- [ ] G3: RPM spec declares var/lib dump directory
  CHECK: bash -c 'grep -q "lib/hpcperfstats/dump" hpcperfstats.spec && echo G3_OK'
  EXPECT: G3_OK
  CWD: HPCPerfStats/monitor

- [ ] G4: rpmspec parses
  CHECK: bash -c 'rpmspec -P hpcperfstats.spec >/dev/null && echo G4_OK'
  EXPECT: G4_OK
  CWD: HPCPerfStats/monitor

- [ ] G5: Static bundle make check
  CHECK: bash -c './scripts/build_static_bundle.sh && make -C .build-static check && echo G5_OK'
  EXPECT: G5_OK
  CWD: HPCPerfStats/monitor
