# Windows RTX 4070 Ti SUPER / MSI Z590 first-start report

Date: 2026-10-25. Scope: one GPU, one manual-U EFI startup, followed by two
instruction-benchmark rounds in Windows.

The exact-board v0.1 UEFI port completed its firmware transaction, PCIe reset and
NVIDIA driver handoff. FP16/FP32 and TF32 instruction throughput increased by
**69.7%** and **71.8%**, respectively, when combining all post-start samples.

## Tested system and source

| Item | Value |
|---|---|
| GPU | RTX 4070 Ti SUPER, AD103, 16 GB |
| PCI / subsystem | `10de:2705` / `6688:2705` |
| VBIOS | `95.03.45.40.a4` |
| Mainboard | MSI MPG Z590 GAMING CARBON WIFI, MS-7D06 |
| Windows | Windows 10 Pro 22H2, build 19045 |
| NVIDIA driver | 617.42 |
| Configured power limit | 300 W |
| Secure Boot | Disabled at capture/startup |
| Tested EFI size | 503,296 bytes |
| Tested EFI SHA-256 | `7ffe978a069647f6e9ac0615ef5b91afc7c8c188ea4514a947b06bfca6c52438` |
| Tested build ID | `9b309fa2bd74e763895023ed40834492c69607a2ced77583d8124e2dff6eb047` |

The port retains the transaction/release/handoff code from the upstream
[`windows/ad104-msi` v0.5.4 source](https://github.com/JustAResearcher/gpu-unlock-ada/tree/4e7fefcc4923f9e083d5324dc75a7d2f906af77f/windows/ad104-msi)
and adapts it to the captured AD103 GPU, MSI Z590 firmware and Windows boot profile.
The Linux source base is `491925c1079435e260137b9f3526066e764de830`. No Linux kernel
implementation changes are part of this Windows port.

Two read-only cold-start captures established PCI configuration, PROM/ROM,
21 GPU registers, the UEFI ownership graph, loaded code for three display drivers,
and the system ESP's boot manager. The raw captures include a non-system NTFS
`OpenVolume: EFI_NOT_FOUND`; an independent Windows partition/boot-file review
identifies that data partition and confirms the successfully captured system ESP.
Raw enumeration/authorization flags remain unchanged. The reviewed exception
belongs to this exact profile and is not a generic permission to ignore errors.

## Transaction and Windows handoff evidence

The verbatim [transaction log](evidence/windows-ad103-2705/transaction.txt) records:

- Payload marker `0x67b00001`, independent required-permission checks and SEC2 halt.
- Entry SS0 `0x60622010` and SS1 `0x7`; exactly one write to each selector, producing
  SS0 `0x88888888` and SS1 `0x8`.
- Effective speed readout full-rate before and after exactly one successful FLR;
  selector values also persist after FLR.
- Firmware containment, GSP halt, bus mastering clear, no pending transaction and
  WPR2 clear-state readback.
- Final transaction `result=0 safe=1 retained=0 started=1 quiesced=1 flrs=1 DMA_live=0`.

Two pre-reset Falcon timeout messages are explicitly tolerated by the retained
NVIDIA-derived path for hardware bug 3419321. Subsequent execution, halt,
containment and cleanup checks passed. The optional FBPA PLM readback remains
`0xffffff0f`; the required WPR_CFG/WPR/FEAT permission checks passed independently.

The final pre-Windows check runs after the USB log is closed. The log's success
line therefore establishes the logged GPU transaction, not Windows startup by
itself. The operator returned to Windows through **U**; the physical GPU reported
ConfigManagerErrorCode 0 / Status OK with driver 617.42, and the matched CUDA
measurements completed. These separate observations support successful handoff.

## Matched instruction measurements

Both states used the same SM89 cubin and launcher, CUDA 11.8 NVRTC/`ptxas`, GPU,
NVIDIA driver, power limit and launch settings. Cubin SHA-256:
`b1274a5aa7a8469bf100b71a49bccb8372457913b692d094ce3692f8f92070a8`.
SASS inspection identified `HMMA.16816.F32` and `HMMA.1688.F32.TF32`.

Settings: 20,000 iterations, 1,056 blocks, 256 threads, eight independent accumulator
chains, and CUDA graph event nodes around the kernel. Each round used a warmup
then three process runs with five timed samples per mode. `nvidia-smi` polling
stopped before timed runs. Normal Windows desktop use continued; clocks were
sampled at run boundaries rather than continuously.

| Mode | Stock: median of 15 samples | Post-start: median of all 30 samples | Change |
|---|---:|---:|---:|
| FP16 inputs / FP32 accumulation | 88.1726 TFLOP/s | 149.5942 TFLOP/s | +69.7% |
| TF32 inputs / FP32 accumulation | 44.3743 TFLOP/s | 76.2210 TFLOP/s | +71.8% |

Both post-start rounds contribute every sample. The full per-sample timings,
per-round summaries and control-mode measurements are in
[benchmark.json](evidence/windows-ad103-2705/benchmark.json). All-one input checks
compared 270,336 output cells per mode and reported zero mismatches throughout.
These checks do not establish general numerical accuracy. BF16 was not directly
benchmarked in this Windows experiment; no VS-DRBA or other application speedup
was measured.

## Software validation and artifact identity

The port passed 177 simulated unlock scenarios: 17 admission, 90 transaction,
15 application-flow, 47 driver-release and eight boot-I/O cases. The real
transaction source runs against EFI/GPU mocks, real locally generated pinned
firmware and private captured PROM/driver-code fixtures. Separate read-only probe
checks cover 17 GPU cases, 13 context cases and 179 platform checks; the 27 public
Python tests cover parsing and independent profile review.

Public preparation removes machine-specific default input paths and updates
build/documentation instructions. The hardware-tested EFI and original source
snapshot remain preserved locally. The public
[tested-build metadata](evidence/windows-ad103-2705/tested-build.json) records the
original build ID, EFI hash and public source hashes. Its false hardware-tested
flags are the original build-time values; later observations are recorded
separately. A rebuild of the publication sources has its own build ID and has not
inherited a hardware test.

## Scope and distribution

This establishes one successful startup on the listed GPU and platform. Repeat
cold/warm starts, sleep/resume, sustained operation, arbitrary numerical workloads
and other boards/firmware remain unverified. Instruction throughput is not an
application-speedup claim.

The [evidence inventory](evidence/windows-ad103-2705/README.md) and SHA-256 list
cover the selected public log, metadata and timing records. ROM/PROM, raw ownership
captures, UEFI driver code, Windows boot-manager bytes, private GUIDs/UUIDs and
compiled EFI/cubin/firmware remain outside Git. See the
[Windows build guide](../windows/README.md) for local-input requirements and
[firmware notice](../firmware/NOTICE.md) for NVIDIA distribution terms.
