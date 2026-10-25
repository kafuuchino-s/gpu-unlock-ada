#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build the read-only RTX 4070 Ti SUPER UEFI probe. Requires no NVIDIA assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "windows"
DEPS = BUILD / "deps"
UPSTREAM_REVISION = "51df9c17ec89eb8486df52cc9079dde4bd1f9974"
UPSTREAM_URL = "https://huggingface.co/TOTORONG/AD103_win_unlocker"
ZIG_VERSION = "0.14.1"
ZIG_ARCHIVE = "zig-x86_64-windows-0.14.1.zip"
ZIG_SHA256 = "554f5378228923ffd558eac35e21af020c73789d87afeabf4bfd16f2e6feed2c"
ZIG_BYTES = 82229343
PLATFORM_REVISION = "4e7fefcc4923f9e083d5324dc75a7d2f906af77f"


def source_hashes() -> dict[str, str]:
    files = [path for path in (ROOT / "windows/uefi").rglob("*")
             if path.is_file() and path.suffix in {".c", ".h", ".md", ".txt"}]
    files.extend(ROOT / relative for relative in (
        "windows/build_probe.py", "windows/test_probe.c", "windows/test_context.c"))
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in sorted(files)}


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def prepare(upstream: Path | None) -> None:
    DEPS.mkdir(parents=True, exist_ok=True)
    headers = DEPS / "edk2"
    if upstream is not None or not (headers / "MdePkg/Include/Uefi.h").is_file():
        checkout = upstream or DEPS / "AD103_win_unlocker"
        env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1")
        if not checkout.exists():
            subprocess.run(["git", "clone", "--no-checkout", UPSTREAM_URL, str(checkout)],
                           check=True, env=env)
            subprocess.run(["git", "-C", str(checkout), "checkout", "--detach", UPSTREAM_REVISION],
                           check=True, env=env)
        revision = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        if revision != UPSTREAM_REVISION:
            raise ValueError(f"EDK2 source checkout must be {UPSTREAM_REVISION}; got {revision}")
        shutil.copytree(checkout / "source/deps/edk2", headers, dirs_exist_ok=True)
        (DEPS / "edk2-source.json").write_text(json.dumps({
            "url": UPSTREAM_URL, "revision": revision,
            "path": "source/deps/edk2", "license": "BSD-2-Clause-Patent (see headers)",
        }, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        return  # On Linux/macOS pass an installed Zig 0.14.1 with --zig.
    archive = DEPS / ZIG_ARCHIVE
    if not archive.is_file() or digest(archive) != ZIG_SHA256:
        partial = archive.with_suffix(".part")
        offset = partial.stat().st_size if partial.exists() else 0
        request = urllib.request.Request(
            f"https://ziglang.org/download/{ZIG_VERSION}/{ZIG_ARCHIVE}",
            headers={"Range": f"bytes={offset}-"} if offset else {})
        with urllib.request.urlopen(request, timeout=25) as response:
            if offset and response.status == 206:
                if not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise ValueError("Unexpected Zig download range")
            else:
                offset = 0
            print(f"Downloading Zig: resuming at {offset}/{ZIG_BYTES} bytes", flush=True)
            with partial.open("ab" if offset else "wb") as output:
                while block := response.read(1024 * 1024):
                    output.write(block)
        if partial.stat().st_size != ZIG_BYTES or digest(partial) != ZIG_SHA256:
            raise ValueError("Zig archive size or SHA-256 mismatch")
        partial.replace(archive)
    executable = DEPS / "zig-x86_64-windows-0.14.1" / "zig.exe"
    if not executable.is_file():
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(DEPS)
    print(f"Verified Zig {ZIG_VERSION}: {executable}", flush=True)


def inspect_pe(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    u16 = lambda offset: struct.unpack_from("<H", data, offset)[0]
    u32 = lambda offset: struct.unpack_from("<I", data, offset)[0]
    pe = u32(60)
    optional = pe + 24
    if data[:2] != b"MZ" or data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("Invalid PE image")
    if u16(pe + 4) != 0x8664 or u16(optional) != 0x20B or u16(optional + 68) != 10:
        raise ValueError("Expected AMD64 PE32+ EFI_APPLICATION")
    if u16(optional + 70) & 0x140 != 0x140:
        raise ValueError("Expected dynamic base and NX compatibility")
    if not u32(optional + 112 + 5 * 8 + 4):
        raise ValueError("Missing relocations")
    for directory in (1, 12, 13):
        start = optional + 112 + directory * 8
        if data[start:start + 8] != bytes(8):
            raise ValueError("EFI must not import Windows libraries")
    entry = u32(optional + 16)
    sections = optional + u16(pe + 20)
    if not any(u32(section + 12) <= entry < u32(section + 12) + u32(section + 8)
               and u32(section + 36) & 0x20000000
               for section in (sections + 40 * i for i in range(u16(pe + 6)))):
        raise ValueError("EFI entry point is outside executable sections")
    return {"machine": "AMD64", "subsystem": "EFI_APPLICATION", "imports": False,
            "relocations": True, "size": len(data), "sha256": digest(path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="Fetch pinned build dependencies")
    parser.add_argument("--deps-only", action="store_true")
    parser.add_argument("--check", action="store_true", help="Run host checks against mocked UEFI protocols")
    parser.add_argument("--upstream", type=Path, help="Existing pinned TOTORONG checkout")
    parser.add_argument("--zig", type=Path)
    parser.add_argument("--edk2", type=Path, help="EDK2 directory containing MdePkg")
    parser.add_argument("--subsystem", default="6688:2705", help="Subsystem vendor:device")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}", args.subsystem):
        parser.error("--subsystem must be four hex digits:four hex digits")
    if args.prepare or args.deps_only:
        prepare(args.upstream.resolve() if args.upstream else None)
    if args.deps_only:
        return
    zig = args.zig or (DEPS / "zig-x86_64-windows-0.14.1/zig.exe" if os.name == "nt"
                       else Path(shutil.which("zig") or "zig"))
    headers = args.edk2 or DEPS / "edk2"
    version = subprocess.check_output([str(zig), "version"], text=True).strip()
    if version != ZIG_VERSION:
        raise ValueError(f"Expected Zig {ZIG_VERSION}; got {version}")
    if not (headers / "MdePkg/Include/Uefi.h").is_file():
        raise ValueError("Missing EDK2 headers: run with --prepare or supply --edk2")
    subsystem_vendor, subsystem_device = (int(value, 16) for value in args.subsystem.split(":"))
    subsystem = subsystem_vendor | subsystem_device << 16
    hashes = source_hashes()
    build_id = hashlib.sha256(json.dumps({
        "sources": hashes, "subsystem": args.subsystem.lower(), "zig": version,
        "edk2": str(args.edk2) if args.edk2 else UPSTREAM_REVISION,
    }, sort_keys=True).encode("utf-8")).hexdigest()
    definitions = [f"-DPROBE_SUBSYSTEM_ID=0x{subsystem:08x}U", f'-DPROBE_BUILD_ID="{build_id}"']
    vendor = ROOT / "windows/uefi/vendor/ad104"
    shared = [ROOT / "windows/uefi/context.c", vendor / "src/core.c", vendor / "src/platform.c"]
    includes = ["-I", str(headers / "MdePkg/Include"),
                "-I", str(headers / "MdePkg/Include/X64")]
    if args.check:
        BUILD.mkdir(parents=True, exist_ok=True)
        for name, test, components in (
            ("probe", ROOT / "windows/test_probe.c", shared),
            ("context", ROOT / "windows/test_context.c", shared[1:]),
            ("platform", vendor / "tests/platform_tests.c", shared[1:]),
        ):
            host_test = BUILD / (f"{name}-host-checks.exe" if os.name == "nt" else f"{name}-host-checks")
            host_command = [str(zig), "cc", "-std=c11", "-fshort-wchar", "-Wall", "-Wextra", "-Werror",
                            *definitions, *includes, str(test),
                            *(str(path) for path in components), "-o", str(host_test)]
            if os.name == "nt":
                host_command[2:2] = ["-target", "x86_64-windows-gnu"]
            subprocess.run(host_command, check=True)
            subprocess.run([str(host_test), *([str(BUILD / "context-host-fixture.json")]
                                            if name == "context" else [])], check=True)
            if name == "context":
                fixture = json.loads((BUILD / "context-host-fixture.json").read_text(encoding="ascii"))
                assert fixture["build_id"] == build_id
                assert fixture["platform"]["complete"] is True
                assert fixture["probe_version"] == 3
                assert fixture["windows_boot"]["saved_count"] == 1
                assert fixture["windows_boot"]["filesystem_errors"] == []
    source = ROOT / "windows/uefi/probe.c"
    stage = BUILD / "probe-usb"
    output = stage / "EFI/BOOT/BOOTX64.EFI"
    output.parent.mkdir(parents=True, exist_ok=True)
    (stage / "GPU-UNLOCK-2705").mkdir(exist_ok=True)
    runtime_object = BUILD / "probe-runtime.obj"
    subprocess.run([str(zig), "cc", "-target", "x86_64-uefi", "-ffreestanding",
                    "-fno-builtin", "-fno-stack-protector", "-mno-red-zone", "-O0",
                    *includes, "-c", str(vendor / "src/runtime.c"),
                    "-o", str(runtime_object)], check=True)
    command = [str(zig), "cc", "-target", "x86_64-uefi", "-std=c11", "-ffreestanding",
               "-fno-builtin", "-fno-stack-protector", "-mno-red-zone", "-fshort-wchar",
               "-Os", "-g0", "-Wall", "-Wextra", "-Werror", "-nostdlib",
               *definitions, *includes, str(source), *(str(path) for path in shared),
               str(runtime_object), "-o", str(output)]
    subprocess.run(command, check=True)
    report = {"kind": "read-only-probe", "probe_version": 3, "context_version": 1,
              "build_id": build_id, "source_files_sha256": hashes,
              "platform_source_revision": PLATFORM_REVISION,
              "unlock_implemented": False,
              "hardware_tested": False, "uefi_vm_tested": False,
              "host_checks_passed": args.check,
              "target": "10de:2705", "pci_subsystem": args.subsystem.lower(),
              "zig_version": version, "edk2_source_revision": UPSTREAM_REVISION
              if not args.edk2 else "user-supplied",
              "source_sha256": digest(source), **inspect_pe(output)}
    report_json = json.dumps(report, indent=2) + "\n"
    (BUILD / "probe-build.json").write_text(report_json, encoding="utf-8")
    (stage / "PROBE-BUILD.json").write_text(report_json, encoding="utf-8")
    (stage / "PROBE-README.txt").write_text(
        "RTX 4070 Ti SUPER read-only UEFI probe v3\n"
        "Captures GPU baseline, UEFI ownership/driver code and Windows boot-file profiles.\n"
        "Windows boot files are read only; new capture files are written to this USB.\n"
        "This is a diagnostic program, not an unlocker. No GPU firmware is loaded.\n"
        f"Target 10de:2705, subsystem {args.subsystem.lower()}.\n"
        "Copy this folder's CONTENTS to an existing FAT32 USB drive.\n"
        "Preserve any existing EFI/BOOT/BOOTX64.EFI. Never copy to the Windows ESP.\n"
        "Boot the USB once. Results: GPU-UNLOCK-2705/RUN00 (or next unused run).\n"
        "After the summary, press a key to return to firmware.\n"
        "Build validation passed; physical execution has not been validated.\n",
        encoding="ascii")
    shutil.copyfile(ROOT / "licenses/GPL-2.0.txt", stage / "COPYING")
    shutil.copyfile(vendor / "LICENSE.txt", stage / "AD104-MIT-LICENSE.txt")
    shutil.copyfile(vendor / "README.md", stage / "THIRD-PARTY.md")
    shutil.copyfile(ROOT / "windows/README.zh-CN.md", stage / "README.zh-CN.md")
    bundle_path = BUILD / "gpu-unlock-2705-readonly-probe.zip"
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for directory in ("EFI/", "EFI/BOOT/", "GPU-UNLOCK-2705/"):
            bundle.writestr(directory, b"")
        for filename in ("EFI/BOOT/BOOTX64.EFI", "PROBE-BUILD.json", "PROBE-README.txt",
                         "README.zh-CN.md", "COPYING", "AD104-MIT-LICENSE.txt", "THIRD-PARTY.md"):
            bundle.write(stage / filename, filename)
    print(json.dumps({"usb_tree": str(stage), "zip": str(bundle_path),
                      "zip_sha256": digest(bundle_path), **report}, indent=2))


if __name__ == "__main__":
    main()
