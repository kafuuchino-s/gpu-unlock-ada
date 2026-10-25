# Exact AD103 2705 Windows UEFI port

Derived from `JustAResearcher/gpu-unlock-ada`, revision
`4e7fefcc4923f9e083d5324dc75a7d2f906af77f`, `windows/ad104-msi` (v0.5.4).
GPL-2.0-only; the original notices and `LICENSE` are retained. That upstream
contains Zagitta's Linux transaction and TOTORONG's Ada payload integration.

Changes made on 2026-10-25 for one captured machine:

- Admit only RTX 4070 Ti SUPER `10de:2705`, subsystem `6688:2705`, full address
  `0000:01:00.0`, AD103 chip `0x193000a1`, and the captured PROM/FWSEC profile.
- Pin the captured 16 GiB memory layout, PCI BARs, cold permission registers,
  SS1 entry value 7, and the exact GPU/GOP device paths on MSI Z590 MS-7D06.
  SS0 is latched at entry and rechecked throughout the one-shot transaction.
- Pin the three loaded UEFI driver code hashes, image sizes, Stop offsets and
  NVGOP version from RUN01. No loaded driver code/data is patched.
- Pin the captured Windows boot-manager length/hash and GPT partition geometry;
  generate the machine's private partition GUID header only under `build/`.
- Use `AD103-2705-ASSETS` for the five pinned firmware files and
  `AD103-2705-UNLOCK/RUNxxxx.TXT` for new USB logs. Existing diagnostic captures
  remain separate and are never overwritten by this port.
- Preserve the upstream manual U choice, 30-second stock Windows default,
  exact ownership/driver checks, bounded unrelated USB graph handling,
  containment/retention rules, and final checks before starting Windows.
- Adapt the retained host tests to the real private PROM, PCI configuration,
  driver code, 523-handle/249-edge graph, boot manager and 16 GiB layout.
  All PCI/MMIO/DMA and firmware effects in those tests are simulated.
- Use the already pinned Zig 0.14.1 and EDK2 headers for a local reproducible
  build. Retained `GuAd104*` and `GU_AD104_TRANSACTION` identifiers name the
  upstream adapter interfaces; PCI admission and all actual board pins are AD103.

This port is experimental. On 2026-10-25, its original v0.1 artifact completed
one manual-U startup on the captured machine: payload/permission/halt checks,
post-FLR selectors and effective readout, containment and DMA cleanup passed;
Windows initialized the NVIDIA driver and ran the matched instruction tests.
The hardware evidence identifies that exact artifact and its source hashes in
[the Windows report](../../docs/windows-ad103-2705-report.md). Publication edits
to documentation and build-input arguments do not revalidate a newly built EFI.
Repeated starts, sleep/resume, broad numerical behavior and application gains
remain unverified. Host tests simulate every GPU/firmware operation.

Post-validation change: backport the GFW PRI-error predicate from
[Zagitta/gpu-unlock PR #72](https://github.com/Zagitta/gpu-unlock/pull/72),
revision `1cb357f62336adb0537ebcc933ec89c3fe636baf`. Reads in the `0xbadfXXXX`
family now return `-EAGAIN` before evaluating completion bits; PCI config failures
and all-ones reads still return `-ENODEV`. The existing polling deadline remains
in force. Add 18 direct status checks and transient/persistent PRI-error cases
through the real transaction source for all three entry-selector fixtures.
These changes affect newly built EFI files; the original hardware-tested v0.1
artifact and its published evidence remain identified by their original hashes.

The raw RUN01 capture still reports incomplete filesystem enumeration. The
separate private profile review permits only its one observed OpenVolume
EFI_NOT_FOUND for an independently identified non-system NTFS data partition.
The system ESP and boot-manager bytes were captured and crosschecked in Windows.
The original capture and its false completeness/authorization flags are retained.

Build/check using `python windows/build_unlock.py --check --run <capture-dir>
--partition-review <review.json> --fwsec <fwsec-analysis.json>` after reviewing
the private inputs with `windows/prepare_2705_profile.py` and preparing the pinned
firmware with `windows/prepare_assets.py`. This build command does not install or
execute UEFI code and does not change boot entries or firmware variables.
