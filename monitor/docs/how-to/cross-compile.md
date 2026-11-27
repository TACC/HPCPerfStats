# Cross-compile the monitor

This page shows monitor developers how to compile and test `hpcperfstatsd` for another CPU family with `scripts/cross_compile_test.sh`, using qemu-user and no root.
Use it when a change compiles on a CPU family the build host is not.

Run a foreign family only when it builds the changed code (policy: `monitor-dual-verify-cross-and-static.mdc`):

- Skip aarch64 for x86-backend-only work, and skip x86_64 for `arm` CPU-backend-only work.
- DCGM GPU code is multi-architecture, so a DCGM GPU diff still needs a foreign family.

## Prerequisites

- Host `make`, `cmake`, `pkg-config` and `curl` or `wget`.
  The qemu source bootstrap may also install Python `tomli` and `ninja` in user space with `python3 -m pip --user`.
- A sysroot per target triplet, or network access so the script can build one (below).
- Host `rpm2cpio` and `cpio` for the RPM fallback layer.

No target builds a PMU library or DCGM at build time (`libdcgm` loads at runtime).
The static bundle pins only libev 4.33 and rabbitmq-c 0.18.1, plus libbpf for the eBPF metric profiler.

## Run a cross-compile smoke

To run the smoke, from `HPCPerfStats/monitor/`:

1. Point the script at a sysroot per foreign target with `SYSROOT_<TRIPLET_SLUG>` (preferred), or `SYSROOT` for every foreign target:

   ```bash
   TARGETS="x86_64-linux-gnu aarch64-linux-gnu riscv64-linux-gnu" \
   SYSROOT_AARCH64_LINUX_GNU="/opt/sysroots/aarch64" \
   SYSROOT_RISCV64_LINUX_GNU="/opt/sysroots/riscv64" \
   ./scripts/cross_compile_test.sh --fail-fast
   ```

   `<TRIPLET_SLUG>` is the triplet in upper case with `-` as `_`.

2. Optional: add `--skip-deps` when each target prefix already holds its dependencies.

3. Optional: add `--force-foreign` to treat a triplet as foreign even when it matches the host.

## Let the script build a Rocky 9 sysroot

When neither `SYSROOT` nor `SYSROOT_<TRIPLET_SLUG>` is set, the script creates a Rocky 9 sysroot under `.build/sysroots/` (`AUTO_CREATE_ROCKY9_SYSROOT=1`, the default):

1. It downloads the official Rocky container-base rootfs and extracts it in user space.
2. When that rootfs lacks `gcc`, `g++`, `ar`, `ranlib` or `strip`, it layers the Rocky Container-Toolbox rootfs over the same sysroot.
3. When compilers are still missing, it downloads selected Rocky toolchain RPMs and unpacks them with `rpm2cpio|cpio` (no root, no package transaction).

The host must reach these URLs:

```text
https://download.rockylinux.org/pub/rocky/<RELEASE>/images/<ARCH>/Rocky-<RELEASE>-Container-Base.latest.<ARCH>.tar.xz
https://download.rockylinux.org/pub/rocky/<RELEASE>/images/<ARCH>/Rocky-<RELEASE>-Container-Toolbox.latest.<ARCH>.tar.xz
```

`<RELEASE>` is the Rocky release and `<ARCH>` the target architecture.

```bash
TARGETS="x86_64-linux-gnu" \
AUTO_CREATE_ROCKY9_SYSROOT=1 \
./scripts/cross_compile_test.sh --force-foreign --fail-fast
```

To use your own rootfs instead, set `ROOTFS_TARBALL` or `ROOTFS_TARBALL_<TRIPLET_SLUG>`:

```bash
TARGETS="x86_64-linux-gnu" \
ROOTFS_TARBALL_X86_64_LINUX_GNU="/path/to/rocky9-x86_64.tar.xz" \
AUTO_CREATE_ROCKY9_SYSROOT=0 \
./scripts/cross_compile_test.sh --force-foreign --fail-fast
```

## How the script builds each triplet

| Aspect                                          | Behaviour                                                                                                                                                                 |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Native triplet (target family matches the host) | Canonical path: `scripts/build_static_bundle.sh`, then `make check`; no qemu for compilation                                                                              |
| Foreign triplet                                 | Host `make` / `cmake` / `pkg-config`; qemu-wrapped target `gcc`, `g++`, `ar`, `ranlib`, `strip` from `SYSROOT/usr/bin`                                                    |
| Missing qemu user emulators                     | Bootstraps a local qemu-user build under `.build/qemu-local` (`LOCAL_QEMU_ROOT`)                                                                                          |
| Privileges                                      | Rootless only: no `sudo`, no podman or docker, no `conmon` or `runc`, no `binfmt_misc` registration                                                                       |
| Architecture detection                          | From the configured triplet, not `uname -m` (user-mode emulation can report the host)                                                                                     |
| Foreign link mode                               | `--disable-all-static`, so tests link the sysroot's dynamic `libc` / `libm` (static `libm` IFUNC resolvers fail under qemu-user)                                          |
| Foreign `make check`                            | Runs test binaries under qemu-user through Automake `LOG_COMPILER`; `QEMU_LD_PREFIX` (from `SYSROOT`) resolves the target `ld-linux` and `libc`, like `qemu-* -L SYSROOT` |
| Foreign configure                               | Adds `--disable-amd-gpu --disable-infiniband --disable-opa --disable-lustre`; `nvidia_gpu` stays on (DCGM and NVML are `dlopen`ed, so vendored headers suffice)           |
| CPU backend                                     | `--with-cpu-counter-backend=auto`: `x86` on x86_64, `arm` on aarch64                                                                                                      |

## Verify the result

The script ends with `make check` passing for every triplet in `TARGETS`; `--fail-fast` stops at the first failure.

## Troubleshooting

- The foreign link fails on `libm` symbols: the build used `--enable-all-static`; foreign triplets need the script's `--disable-all-static`.
- Sysroot creation fails offline: set `SYSROOT_<TRIPLET_SLUG>` or `ROOTFS_TARBALL_<TRIPLET_SLUG>` and `AUTO_CREATE_ROCKY9_SYSROOT=0`.

## Related

- [Build the RPM](build-the-rpm.md)
- [x86 counters and roofline](../explanation/x86-counters-and-roofline.md)
- [Monitor landing page](../../README.md)
