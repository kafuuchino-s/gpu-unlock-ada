#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build and host-check the exact 2705/Z590 experimental Windows UEFI port.

All GPU execution in --check is simulated. Bootable output remains under build/;
this tool neither installs it nor changes firmware/Windows boot configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess

from build_probe import DEPS, ZIG_VERSION, digest, inspect_pe
from prepare_2705_profile import prepare

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "windows/unlock"
BUILD = ROOT / "build/windows/unlock"
REVISION = "4e7fefcc4923f9e083d5324dc75a7d2f906af77f"
CORE = ["port", "compat", "checks", "profile", "sha256", "vendor/unlock_610",
        "vendor/prep_610", "vendor/layout_610", "vendor/vbios_610", "vendor/gfw_eval"]
ASSETS = ("booter_load_ad102.bin", "v67-ad102-610.43.03-four-plm.bin", "gsp_ga10x.bin",
          "gsp_boot_ad102.bin", "gsp_boot_desc_ad102.bin")


def source_hashes() -> dict[str, str]:
    files = [p for p in SOURCE.rglob("*") if p.is_file()]
    files += [ROOT / "windows" / name for name in (
        "build_unlock.py", "prepare_2705_profile.py", "prepare_assets.py", "zstd_decode.zig")]
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(files)}


def fixtures(run: Path, private: Path) -> None:
    graph = json.loads((run / "CONTEXT.JSON").read_text(encoding="ascii"))["platform"]
    if not graph["complete"] or graph["handle_count"] != 523 or graph["edge_count"] != 249:
        raise ValueError("Unexpected private RUN01 fixture graph")
    lines = ["/* Private RUN01 ownership fixture. No driver binary code. */",
             f"#define CAPTURED_HANDLE_COUNT {graph['handle_count']}",
             "static const EDGE CapturedEdges[]={"]
    count = 0
    for handle in graph["handles"]:
        for edge in handle["active_opens"]:
            parts = struct.unpack("<IHH8B", bytes.fromhex(edge["protocol"]))
            guid = "{0x%x,0x%x,0x%x,{%s}}" % (*parts[:3], ",".join(hex(x) for x in parts[3:]))
            ident = lambda value: "NULL" if value is None else f"(EFI_HANDLE){value + 1}"
            lines.append("{%s,%s,%s,%s,0x%x,%d}," % (
                ident(handle["id"]), ident(edge["agent"]), ident(edge["controller"]),
                guid, int(edge["attributes"], 16), edge["count"]))
            count += 1
    if count != 249:
        raise ValueError("Ownership edge count mismatch")
    (private / "capture_graph.h").write_text("\n".join(lines + ["};", ""]), encoding="ascii")
    paths = ["/* Private RUN01 USB path fixture. */",
             "static const struct {EFI_HANDLE handle;UINT8 path[128];} CapturedUsbPaths[]={"]
    prefix = "02010c00d041030a00000000010106000014"
    for handle in graph["handles"]:
        path = handle.get("path") or ""
        if path.startswith(prefix):
            data = bytes.fromhex(path)
            if len(data) > 128:
                raise ValueError("USB fixture path too large")
            paths.append("{(EFI_HANDLE)%d,{%s}}," % (handle["id"] + 1, ",".join(hex(x) for x in data)))
    (private / "capture_usb_paths.h").write_text("\n".join(paths + ["};", ""]), encoding="ascii")
    config = struct.unpack("<64I", (private / "PCI-CONFIG.BIN").read_bytes())
    (private / "capture_config.h").write_text(
        "/* Private captured PCI config. */\nstatic const UINT32 CapturedConfig[64]={\n" +
        ",\n".join(",".join(f"0x{x:08x}U" for x in config[i:i + 4]) for i in range(0, 64, 4)) +
        "\n};\n", encoding="ascii")
    shutil.copyfile(private / "boot_profile.h", private / "boot_profile_private.h")


def run_logged(command: list[str], name: str) -> None:
    log = BUILD / f"{name}.log"
    print(f"{name} ...", flush=True)
    with log.open("w", encoding="utf-8") as output:
        result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, cwd=SOURCE)
    text = log.read_text(encoding="utf-8", errors="replace")
    if result.returncode:
        print("\n".join(text.splitlines()[-55:]), flush=True)
        raise RuntimeError(f"{name} failed ({result.returncode}); see {log}")
    print(text.splitlines()[-1] if text.splitlines() else f"{name} passed", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Run all host integration tests")
    parser.add_argument("--only-test", choices=("guards", "efi-transaction", "app-flow", "msi-release", "boot-io"))
    parser.add_argument("--run", type=Path, required=True,
                        help="Reviewed v3 capture directory; original captures stay private")
    parser.add_argument("--private", type=Path, default=ROOT / "build/windows/ad103-2705-profile")
    parser.add_argument("--partition-review", type=Path, required=True,
                        help="Independent Windows partition/boot-file crosscheck JSON")
    parser.add_argument("--fwsec", type=Path, required=True,
                        help="Offline FWSEC analysis JSON for the captured PROM")
    parser.add_argument("--assets", type=Path, default=ROOT / "build/windows/assets")
    parser.add_argument("--zig", type=Path, default=DEPS / "zig-x86_64-windows-0.14.1/zig.exe")
    args = parser.parse_args()
    if subprocess.check_output([str(args.zig), "version"], text=True).strip() != ZIG_VERSION:
        raise ValueError(f"Requires Zig {ZIG_VERSION}")
    BUILD.mkdir(parents=True, exist_ok=True)
    profile = prepare(args.run, args.partition_review, args.fwsec, args.private)
    fixtures(args.run, args.private)
    headers = DEPS / "edk2/MdePkg/Include"
    flags = ["-std=c11", "-O2", "-fshort-wchar", "-Wall", "-Wextra", "-Werror",
             "-Wno-unused-parameter", "-Wno-misleading-indentation", "-Wno-macro-redefined",
             "-D__KERNEL__", "-DGU_UEFI_PORT", "-DGU_AD104_TRANSACTION", "-DGU_ENTRY_LATCHED_SS1_FULL",
             "-I", str(SOURCE / "compat"), "-I", str(headers), "-I", str(headers / "X64"),
             "-I", str(args.private.resolve())]
    tests = [
        ("guards", ["tests/test_guards.c", *(f"{s}.c" for s in CORE)], [str(args.private / "PROM.BIN"), str(args.assets)]),
        ("efi-transaction", ["tests/test_efi_transaction.c", *(f"{s}.c" for s in CORE)], [str(args.private / "PROM.BIN"), str(args.assets)]),
        ("app-flow", ["tests/test_app_flow.c"], []),
        ("msi-release", ["tests/test_msi_release.c", "sha256.c", "format.c"], [str(args.private)]),
        ("boot-io", ["tests/test_boot_io.c", "sha256.c", "format.c"], [str(args.private / "BOOTMGFW00.BIN"), str(args.assets)]),
    ]
    if args.check or args.only_test:
        for name, sources, arguments in tests:
            if args.only_test and name != args.only_test:
                continue
            executable = BUILD / f"test-{name}.exe"
            run_logged([str(args.zig), "cc", "-target", "x86_64-windows-gnu", *flags,
                        "-D_CRT_SECURE_NO_WARNINGS", *(str(SOURCE / s) for s in sources), "-o", str(executable)], f"compile-{name}")
            run_logged([str(executable), *arguments], name)
    if args.only_test:
        return
    hashes = source_hashes()
    build_id = hashlib.sha256(json.dumps({"sources": hashes,
        "private_files": profile["private_files_sha256"], "capture": profile["capture_sha256"],
        "zig": ZIG_VERSION}, sort_keys=True).encode()).hexdigest()
    objects = []
    for source in [*CORE, "app", "boot_io", "msi_release", "runtime", "format"]:
        obj = BUILD / (source.replace("/", "_") + ".obj")
        command = [str(args.zig), "cc", "-target", "x86_64-uefi", *flags,
                   "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-mno-red-zone",
                   "-mno-stack-arg-probe", "-g0", f'-DGU_BUILD_ID="{build_id}"']
        if source == "runtime":
            command += ["-O0"]
        command += ["-c", str(SOURCE / (source + ".c")), "-o", str(obj)]
        run_logged(command, "compile-" + source.replace("/", "_"))
        objects.append(str(obj))
    stage = BUILD / "usb"
    output = stage / "EFI/BOOT/BOOTX64.EFI"
    output.parent.mkdir(parents=True, exist_ok=True)
    run_logged([str(args.zig), "cc", "-target", "x86_64-uefi", "-nostdlib", *objects,
                "-o", str(output)], "link-efi")
    manifest = {"kind": "exact-board-experimental-unlocker", "version": "0.1", "build_id": build_id,
        "target": "10de:2705", "pci_subsystem": "6688:2705", "platform": "MSI Z590 MS-7D06",
        "upstream_revision": REVISION, "source_files_sha256": hashes,
        "gpu_write_capability": True, "automatic_unlock": False, "requires_key": "U",
        "default": "stock Windows after 30 seconds", "single_attempt": True,
        "host_checks_passed": bool(args.check), "hardware_tested": False, "hardware_unlock_validated": False,
        "raw_capture_complete": profile["raw_capture_complete"],
        "reviewed_boot_exception": profile["windows_boot_review"]["review_reason"],
        "private_profile_sha256": digest(args.private / "profile.json"),
        "private_boot_header_sha256": digest(args.private / "boot_profile_private.h"),
        "zig_version": ZIG_VERSION, **inspect_pe(output)}
    target = stage / "AD103-2705-ASSETS"
    target.mkdir(exist_ok=True)
    for name in ASSETS:
        shutil.copyfile(args.assets / name, target / name)
    shutil.copyfile(args.assets / "NVIDIA-LICENSE", stage / "NVIDIA-LICENSE")
    shutil.copyfile(ROOT / "firmware/NOTICE.md", stage / "FIRMWARE-NOTICE.md")
    shutil.copyfile(SOURCE / "LICENSE", stage / "COPYING")
    shutil.copyfile(SOURCE / "CHANGES.md", stage / "UNLOCK-SOURCE.md")
    (stage / "AD103-2705-UNLOCK").mkdir(exist_ok=True)
    manifest["asset_files_sha256"] = {name: digest(target / name) for name in ASSETS}
    text = json.dumps(manifest, indent=2) + "\n"
    (BUILD / "unlock-build.json").write_text(text, encoding="utf-8")
    (stage / "UNLOCK-BUILD.json").write_text(text, encoding="utf-8")
    print(json.dumps({"output": str(output), "sha256": manifest["sha256"],
        "host_checks_passed": manifest["host_checks_passed"], "hardware_tested": False}, indent=2), flush=True)


if __name__ == "__main__":
    main()
