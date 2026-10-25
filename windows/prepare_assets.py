#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prepare pinned Ada firmware using the already pinned Zig host toolchain."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "firmware"))
from extract_boot_assets import ADA_PINNED, extract_assets
from fetch_artifacts import fetch, inputs
from generate_v67 import build_ad102_payload, AD102_PAYLOAD_SHA256, read_member


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=ROOT / "build/windows/inputs")
    parser.add_argument("--output", type=Path, default=ROOT / "build/windows/assets")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--zig", type=Path, default=ROOT / "build/windows/deps/zig-x86_64-windows-0.14.1/zig.exe")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "firmware/artifacts-610.43.03.json").read_text())
    source, package = [fetch(entry, args.inputs, args.offline) for entry in inputs(manifest)]
    generated = ROOT / "build/windows/deps/nvidia-generated"
    with tarfile.open(source, "r:xz") as archive:
        for name in ADA_PINNED:
            suffix = "/src/nvidia/generated/" + name
            members = [m for m in archive.getmembers() if ("/" + m.name).endswith(suffix)]
            if len(members) != 1 or not members[0].isfile() or members[0].size > 16 * 1024 * 1024:
                raise ValueError("Missing, duplicate or oversized pinned generated source")
            stream = archive.extractfile(members[0])
            if stream is None:
                raise ValueError("Cannot read pinned generated source")
            data = stream.read()
            if hashlib.sha256(data).hexdigest() != ADA_PINNED[name]:
                raise ValueError("Generated NVIDIA source hash mismatch")
            destination = generated / "src/nvidia/generated" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
    extract_assets(generated, args.output, "ad102")
    firmware = manifest["source_artifacts"][2]["firmware"][0]
    notice = manifest["source_artifacts"][2]["notice"]
    unpacked = args.inputs / (package.name + ".tar")
    if not unpacked.exists():
        decoder = ROOT / "build/windows/zstd-decode.exe"
        subprocess.run([str(args.zig), "build-exe", str(ROOT / "windows/zstd_decode.zig"),
                        "-O", "ReleaseFast", f"-femit-bin={decoder}"], check=True)
        temporary = unpacked.with_suffix(".temporary")
        try:
            subprocess.run([str(decoder), str(package), str(temporary)], check=True)
            temporary.replace(unpacked)
        finally:
            temporary.unlink(missing_ok=True)
    for entry, filename in ((firmware, "gsp_ga10x.bin"), (notice, "NVIDIA-LICENSE")):
        with tarfile.open(unpacked, "r:") as member_archive:
            data = read_member(member_archive, entry["path"])
        if hashlib.sha256(data).hexdigest() != entry["sha256"] \
                or ("size" in entry and len(data) != entry["size"]):
            raise ValueError(f"Extracted package member hash/size mismatch: {filename}")
        (args.output / filename).write_bytes(data)
    payload = build_ad102_payload()
    if hashlib.sha256(payload).hexdigest() != AD102_PAYLOAD_SHA256:
        raise ValueError("Generated Ada payload hash mismatch")
    (args.output / "v67-ad102-610.43.03-four-plm.bin").write_bytes(payload)
    print(json.dumps({p.name: {"bytes": p.stat().st_size,
                               "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                      for p in sorted(args.output.iterdir()) if p.is_file()}, indent=2))


if __name__ == "__main__":
    main()
