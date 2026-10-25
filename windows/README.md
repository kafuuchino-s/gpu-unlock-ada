# Experimental Windows UEFI port: RTX 4070 Ti SUPER / MSI Z590

[中文说明](README.zh-CN.md) ·
[Hardware report](../docs/windows-ad103-2705-report.md) ·
[Public evidence](../docs/evidence/windows-ad103-2705/README.md)

This source-only port completed one manual-U startup on 2026-10-25 using an
RTX 4070 Ti SUPER (`10de:2705`, subsystem `6688:2705`, VBIOS `95.03.45.40.a4`),
MSI MPG Z590 GAMING CARBON WIFI (MS-7D06), Windows 10 Pro 22H2 build 19045 and
NVIDIA 617.42. Matched instruction tests measured FP16/FP32 at **88.17 → 149.59
TFLOP/s** and TF32 at **44.37 → 76.22 TFLOP/s**, using the median of all samples.

The result covers this exact machine and one startup. Repeated cold/warm starts,
sleep/resume, general numerical accuracy and application speedups remain untested.
No ready-to-run EFI, NVIDIA firmware, ROM, Windows boot-manager binary or loaded
UEFI driver code is distributed.

## Components

| Path | Purpose |
|---|---|
| `uefi/`, `build_probe.py` | Separate read-only v3 GPU/UEFI/boot-file collector; no GPU writes, firmware launch, DMA or reset |
| `read_probe.py` | Bounded offline capture/ROM/code/boot-file validation |
| `prepare_2705_profile.py` | Review this exact capture and generate private build/test inputs |
| `prepare_assets.py`, `zstd_decode.zig` | Verify pinned NVIDIA inputs and generate five runtime assets locally |
| `unlock/`, `build_unlock.py` | Exact-machine EFI transaction, driver release, Windows handoff and simulated checks |
| `benchmark_tensor.py` | Same-cubin Windows instruction baseline and post-start comparison |

The unlocker requires the exact PCI/subsystem/chip identity, cold registers,
PROM/FWSEC, loaded UEFI driver code and ownership, GPU/GOP device paths, and
Windows ESP/boot-manager profile. SS0 is latched at entry and rechecked throughout
the attempt. A PCI-ID edit alone does not adapt another machine.

The tested capture contains one `OpenVolume: EFI_NOT_FOUND` on a non-system NTFS
data partition. Independent Windows metadata identifies that partition and the
successfully read system ESP. `prepare_2705_profile.py` accepts only this reviewed
case; additional or different errors require review. Original captures keep their
false enumeration-completeness and authorization flags.

## Build dependencies and read-only checks

Run from the repository root on Windows with Python 3.11+ and Git:

```powershell
python windows/build_probe.py --deps-only
python windows/build_probe.py --check
python -m unittest discover -s tests -p 'test_windows_*.py' -v
```

Dependency preparation pins Zig **0.14.1** with its archive SHA-256 and EDK2 headers
from TOTORONG revision `51df9c17ec89eb8486df52cc9079dde4bd1f9974`. It places them
under ignored `build/windows/deps/`. The Python checks use synthetic data; the
probe host checks mock UEFI services. They need no GPU or private capture.

The collector stages `build/windows/probe-usb/`. Copy its contents to an existing
FAT32 removable USB after preserving its previous `EFI/BOOT/BOOTX64.EFI`. On this
MSI board, a full shutdown and approximately 30 seconds without AC power precedes
F11 → the USB's UEFI entry. The collector creates a new
`GPU-UNLOCK-2705/RUNxx/` and returns to firmware; use the normal Windows Boot Manager
to return to Windows. Offline parsing is:

```powershell
python windows/read_probe.py E:\GPU-UNLOCK-2705\RUN01
```

A zero probe status means collection/saving finished; offline completeness and
profile admission are separate checks. Captures contain private binaries and
partition/device information; keep them on the USB or under ignored `build/`.

## Exact-machine unlock build

The private inputs are deliberately absent from Git:

- A reviewed v3 capture: `REPORT.TXT`, `CONTEXT.JSON`, `PCI-CONFIG.BIN`, `PROM.BIN`,
  `PCI-ROM.BIN` and `BOOTMGFW00.BIN`.
- An independent Windows partition/boot-file crosscheck, with the `partitions`
  and `windows_boot_file` fields consumed by `review_boot()` in
  `prepare_2705_profile.py`. Partition GUIDs and geometry must match the captured
  device paths. System-ESP identity, the non-system NTFS failure, and the installed
  boot-manager hash/length are checked independently.
- An offline FWSEC analysis of that PROM: `parser_result`,
  `physical_prom_sha256`, and the `fwsec` geometry consumed by `prepare()`.
  The retained upstream offline inspector is at
  [`windows/ad104-msi/tools/fwsec_inspect.c`](https://github.com/JustAResearcher/gpu-unlock-ada/blob/4e7fefcc4923f9e083d5324dc75a7d2f906af77f/windows/ad104-msi/tools/fwsec_inspect.c).

The public scripts contain the tested board's hashes and geometry, not its private
bytes. Supplying arbitrary captures will not generate a different supported profile.
Another board, BIOS, driver-code image, boot-manager update or partition layout
needs a separate reviewed adaptation.

After preparing those local inputs, generate the firmware and run all host suites:

```powershell
python windows/prepare_assets.py
python windows/build_unlock.py --check --run build/windows/captures/RUN01 --partition-review build/windows/partition-review.json --fwsec build/windows/fwsec-analysis.json
```

Replace the three input paths with the reviewed local files. Firmware preparation
uses the repository's pinned **610.43.03** archives, source-member and output hashes;
`--offline` reuses verified cached inputs. This firmware version is independent of
the Windows driver version. No pip dependencies or MSVC compiler are needed.

Private profiles/headers, test executables, EFI output, firmware, licenses and build
manifests stay under `build/windows/`. Full EFI checks require those private inputs
and generated firmware; the public Python/probe checks above run without them.
The 183 simulated unlock scenarios cover admission, the real transaction source,
application flow, driver ownership/release and boot-file handling. Another 18 GFW
status checks cover ready/pending, inaccessible and PRI-error samples. The
transaction cases include a transient PRI error followed by successful completion
and a persistent PRI error that times out before starting GPU firmware.
Build/check commands do not install or execute EFI code or change firmware boot
variables.

The staged unlock tree is `build/windows/unlock/usb/`. Its build manifest records
its own source hashes and build ID. `hardware_tested: false` is a build-time fact;
our later hardware report identifies the exact tested v0.1 EFI separately.
The current source includes the GFW PRI-error polling fix from
[Zagitta PR #72](https://github.com/Zagitta/gpu-unlock/pull/72). It keeps polling on
`0xbadfXXXX` register reads within the existing deadline. The hardware report
covers the original artifact, which predates this fix; a newly built EFI requires
its own hardware validation.

## Manual startup and verification

Use a reviewed build for the exact captured machine. Keep USB peripherals in the
reviewed arrangement and use a full power-off before the first attempt. Preserve
the USB's old EFI and use its removable-media boot path; the internal ESP and
BootOrder are not modified by these scripts.

The unlocker offers **U** for one physical attempt, **S**/Esc for stock Windows,
and defaults to stock Windows after 30 seconds. It preloads the exact boot manager,
verifies GPU/display ownership and loaded driver code, releases the three reviewed
drivers, executes the GPU transaction, checks containment, and hands off to Windows.
There is no automatic unlock or retry. Uncertain containment retains DMA resources
and stops. After a STOP or failure to reach Windows, fully power off before using
the original Windows Boot Manager and reviewing the saved log.

Logs use `AD103-2705-UNLOCK/RUNxxxx.TXT`. Require payload marker `0x67b00001`,
independent required permissions, SEC2 halt, selector/readout persistence after
FLR, safe DMA cleanup and a healthy Windows driver. Starting Windows alone is
insufficient. The final pre-Windows check runs after the USB log is closed; the
report distinguishes logged hardware checks from user/driver/CUDA handoff evidence.

## Matched instruction measurements

With an installed CUDA toolkit (`nvcc`/NVRTC/`ptxas`) and NVIDIA driver:

```powershell
python windows/benchmark_tensor.py --label baseline
# After independently checking the UEFI log and Windows driver:
python windows/benchmark_tensor.py --label after-attempt
```

The published comparison used CUDA Toolkit 11.8, the same SM89 cubin, GPU, driver,
300 W power limit and launch settings. Each round has a warmup followed by three
process runs of five samples per mode; the post-start result combines both rounds.
All output cells are checked for the all-one inputs. Formal timed runs have no
concurrent `nvidia-smi` polling. The wrapper records GPU identity and settings
locally and rejects an incompatible comparison. It does not prove unlock state
and does not change clocks, power limits, drivers or boot settings.

## Attribution and distribution

The unlock implementation derives from the GPL-2.0-only
[`windows/ad104-msi` v0.5.4 source at 4e7fefc](https://github.com/JustAResearcher/gpu-unlock-ada/tree/4e7fefcc4923f9e083d5324dc75a7d2f906af77f/windows/ad104-msi),
including Zagitta's Linux transaction and TOTORONG's Ada payload integration.
See [unlock/CHANGES.md](unlock/CHANGES.md) for the exact-board changes. Historical
`GuAd104*` interface names are retained; actual admission targets AD103 `2705`.

The unmodified read-only platform sampler in `uefi/vendor/ad104/` retains its MIT
license. New integration/scripts use GPL-2.0-only. EDK2 headers retain their own
BSD-2-Clause-Patent notices. NVIDIA assets have separate terms; local preparation
keeps the license and [firmware notice](../firmware/NOTICE.md) with the staged tree.
Source distribution excludes the private inputs and generated binaries.
