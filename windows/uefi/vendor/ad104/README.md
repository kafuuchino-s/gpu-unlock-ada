# Vendored read-only platform sampler

Source: https://github.com/JustAResearcher/gpu-unlock-ada
Revision: `4e7fefcc4923f9e083d5324dc75a7d2f906af77f`
Path: `windows/ad104-msi/diagnostic/`
Licence: MIT, copyright (c) 2026 AD104 probe contributors; see `LICENSE.txt`.

`src/core.[ch]`, `src/platform.[ch]`, `src/runtime.c` and
`tests/platform_tests.c` are copied without changes. The local 2705 probe uses
only the JSON, hash, device-path and read-only platform sampler functions.
The AD104-specific `ExactTarget()` utility is unused by the local probe;
PCI admission and all GPU reads remain in `../../probe.c` with its exact
2705/subsystem/chip checks. No upstream unlock transaction is linked.

The sampler observes protocol ownership and bounded loaded driver code. Its
snapshot is not atomic and never authorizes releasing a driver. Captured
proprietary driver bytes and machine-specific paths stay in private captures.

EDK II headers remain external build dependencies from TOTORONG revision
`51df9c17ec89eb8486df52cc9079dde4bd1f9974`, retaining their original
BSD-2-Clause-Patent notices. No EDK II library binary is linked.
