# Windows AD103 2705 evidence inventory

Experiment: 2026-10-25, one manual-U Windows startup on RTX 4070 Ti SUPER
`10de:2705` / subsystem `6688:2705`, MSI Z590 MS-7D06, Windows 10 Pro 22H2,
NVIDIA 617.42. See the [hardware report](../../windows-ad103-2705-report.md).

| File | Content |
|---|---|
| `transaction.txt` | Verbatim USB transaction log, preserved byte-for-byte |
| `hardware-validation.json` | Selected log checks, Windows driver/handoff observations and scope |
| `tested-build.json` | Selected original build metadata, public source/asset hashes and historical flags |
| `benchmark.json` | Baseline and both post-start rounds, all timed samples/checks, settings and summaries |
| `SHA256SUMS` | SHA-256 of the four evidence files above |

The positive-result summary combines all 30 post-start samples per mode; stock has
15. No post-start round is discarded. Numeric benchmark fields are preserved from
the local reports; their original report hashes are included. Export removes GPU
UUIDs, host paths and unrelated telemetry. Local private reviews and complete
captures are not part of this public inventory.

The original EFI is identified by its SHA-256 and build ID, not distributed.
`original_build_time_flags` remain as recorded before the real startup. Publication
changes to documentation/build-input arguments create a different build identity;
the source-hash list identifies the original files, including earlier versions of
`windows/build_unlock.py` and `windows/unlock/CHANGES.md`.

Excluded: ROM/PROM, raw UEFI ownership captures, loaded driver bytes, boot-manager
bytes, partition GUIDs, GPU UUID/device-instance suffix, host paths, firmware/input
archives and compiled EFI/cubin. NVIDIA firmware must be prepared locally under
its separate license. One startup and all-one instruction checks do not establish
repeatability, general numerical accuracy or application gains.

Verify the files from this directory with:

```sh
sha256sum -c SHA256SUMS
```

`.gitattributes` uses `-text` for this directory so checksums cover exact bytes.
