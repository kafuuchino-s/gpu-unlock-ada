#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Record a Windows SM89 baseline and repeat the exact cubin after an unlock attempt.

Uses the existing register-resident instruction probe and CUDA Driver API runner.
NVRTC and ptxas avoid requiring an installed MSVC host compiler. No clock, power,
firmware, registry, driver, or boot configuration is changed.
"""
from __future__ import annotations

import argparse
import ctypes as C
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build/windows/benchmarks"
SOURCE = ROOT / "diagnostics/bench/tensor_modes_issue.cu"
RUNNER = ROOT / "diagnostics/bench/tensor_modes_driver_runner.py"
GPU_FIELDS = ("uuid", "name", "pci.bus_id", "driver_version", "power.limit",
              "clocks.current.sm", "clocks.current.memory", "utilization.gpu",
              "temperature.gpu", "power.draw", "pstate")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def gpu_snapshot() -> dict[str, str]:
    raw = subprocess.check_output(["nvidia-smi", "--query-gpu=" + ",".join(GPU_FIELDS),
                                   "--format=csv,noheader,nounits"], text=True, timeout=15)
    lines = raw.strip().splitlines()
    if len(lines) != 1:
        raise ValueError("This comparison requires exactly one NVIDIA GPU")
    fields = [x.strip() for x in lines[0].split(",")]
    if len(fields) != len(GPU_FIELDS):
        raise ValueError(f"Unexpected GPU metadata: {raw!r}")
    return dict(zip(GPU_FIELDS, fields))


def build(toolkit: Path) -> dict:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUTPUT / "probe-build.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["source_sha256"] != sha256(SOURCE):
            raise ValueError("Probe source changed; preserve the old baseline before rebuilding")
        for name, expected in manifest["files_sha256"].items():
            if sha256(OUTPUT / name) != expected:
                raise ValueError(f"Existing comparison binary changed: {name}")
        return manifest
    dlls = list((toolkit / "bin").glob("nvrtc64_*.dll"))
    if len(dlls) != 1:
        raise ValueError(f"Cannot identify one NVRTC DLL in {toolkit / 'bin'}")
    program = C.c_void_p()
    with os.add_dll_directory(str(toolkit / "bin")):
        lib = C.WinDLL(str(dlls[0]))
        def api(name, arguments):
            function = getattr(lib, name)
            function.argtypes = arguments
            function.restype = C.c_int
            return function
        def check(code):
            if code:
                raise RuntimeError(f"NVRTC error {code}; see nvrtc.log")
        version = api("nvrtcVersion", [C.POINTER(C.c_int), C.POINTER(C.c_int)])
        create = api("nvrtcCreateProgram", [C.POINTER(C.c_void_p), C.c_char_p, C.c_char_p,
                                            C.c_int, C.POINTER(C.c_char_p), C.POINTER(C.c_char_p)])
        compile_program = api("nvrtcCompileProgram", [C.c_void_p, C.c_int, C.POINTER(C.c_char_p)])
        log_size = api("nvrtcGetProgramLogSize", [C.c_void_p, C.POINTER(C.c_size_t)])
        get_log = api("nvrtcGetProgramLog", [C.c_void_p, C.c_void_p])
        ptx_size = api("nvrtcGetPTXSize", [C.c_void_p, C.POINTER(C.c_size_t)])
        get_ptx = api("nvrtcGetPTX", [C.c_void_p, C.c_void_p])
        destroy = api("nvrtcDestroyProgram", [C.POINTER(C.c_void_p)])
        major, minor = C.c_int(), C.c_int()
        check(version(C.byref(major), C.byref(minor)))
        # This source uses no stdint types; supply the unused include virtually.
        # The complete CUDA source bytes remain unchanged and are hash-recorded.
        headers = (C.c_char_p * 1)(b"#pragma once\n")
        names = (C.c_char_p * 1)(b"stdint.h")
        check(create(C.byref(program), SOURCE.read_bytes(), SOURCE.name.encode(), 1, headers, names))
        options = [b"--gpu-architecture=compute_89", b"--std=c++17"]
        try:
            status = compile_program(program, len(options), (C.c_char_p * len(options))(*options))
            length = C.c_size_t()
            check(log_size(program, C.byref(length)))
            log = C.create_string_buffer(length.value)
            check(get_log(program, log))
            (OUTPUT / "nvrtc.log").write_bytes(log.value)
            check(status)
            check(ptx_size(program, C.byref(length)))
            text = C.create_string_buffer(length.value)
            check(get_ptx(program, text))
            (OUTPUT / "tensor-modes-sm89.ptx").write_bytes(text.value)
        finally:
            check(destroy(C.byref(program)))
    ptxas = toolkit / "bin/ptxas.exe"
    command = [str(ptxas), "-arch=sm_89", "-O3", "-v", "-o",
               str(OUTPUT / "tensor-modes-sm89.cubin"), str(OUTPUT / "tensor-modes-sm89.ptx")]
    result = subprocess.run(command, capture_output=True, text=True)
    (OUTPUT / "ptxas.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"ptxas failed: {result.stderr}")
    manifest = {"source": SOURCE.relative_to(ROOT).as_posix(), "source_sha256": sha256(SOURCE),
        "nvrtc_version": [major.value, minor.value], "nvrtc_dll_sha256": sha256(dlls[0]),
        "nvrtc_options": [x.decode() for x in options], "virtual_header": "unused stdint.h: #pragma once",
        "ptxas_version": subprocess.check_output([str(ptxas), "--version"], text=True),
        "files_sha256": {name: sha256(OUTPUT / name) for name in
                          ("tensor-modes-sm89.ptx", "tensor-modes-sm89.cubin")}}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", choices=("baseline", "after-attempt"), required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--runs", type=int, default=3, choices=range(1, 6))
    nvcc = shutil.which("nvcc")
    parser.add_argument("--toolkit", type=Path, default=Path(nvcc).parents[1] if nvcc else None)
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    if os.name != "nt" or args.toolkit is None:
        parser.error("Use Windows with the CUDA toolkit, or specify --toolkit")
    binary = build(args.toolkit)
    if args.build_only:
        print(json.dumps(binary, indent=2))
        return
    before = gpu_snapshot()
    if "4070 Ti SUPER" not in before["name"]:
        raise ValueError("The current comparison was prepared for the RTX 4070 Ti SUPER")
    cubin_hash = binary["files_sha256"]["tensor-modes-sm89.cubin"]
    settings = {"uuid": before["uuid"], "driver": before["driver_version"],
        "power_limit_w": float(before["power.limit"]), "cubin_sha256": cubin_hash,
        "runner_sha256": sha256(RUNNER), "runs": args.runs, "iters": 20000, "reps": 5,
        "blocks": 1056, "threads": 256, "chains": 8, "timing_mode": "graph-event-nodes",
        "telemetry_policy": "warmup polling and between-run snapshots"}
    baseline = None
    if args.label == "after-attempt":
        pointer = OUTPUT / "latest-baseline.json"
        path = args.compare or Path(json.loads(pointer.read_text(encoding="utf-8"))["report"])
        baseline = json.loads(path.read_text(encoding="utf-8"))
        if baseline["settings"] != settings:
            raise ValueError("GPU/driver/power/binary/launch settings differ from the baseline")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = OUTPUT / f"{args.label}-{stamp}"
    run_dir.mkdir()
    command = [sys.executable, str(RUNNER), str(OUTPUT / "tensor-modes-sm89.cubin"),
        "--sha256", cubin_hash, "--expect-uuid", before["uuid"],
        "--expect-power-limit", before["power.limit"], "--iters", "20000", "--reps", "5",
        "--cuda-graph"]
    stop = threading.Event()
    telemetry = []
    def sample():
        while not stop.is_set():
            try:
                telemetry.append({"utc": datetime.now(timezone.utc).isoformat(), **gpu_snapshot()})
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                telemetry.append({"error": str(error)})
            stop.wait(0.5)
    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    reports = []
    try:
        for index in range(args.runs + 1):
            name = "warmup" if index == 0 else f"run{index:02d}"
            print(f"{name}: FP16/FP32, TF32, unsigned INT4", flush=True)
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            (run_dir / f"{name}.stderr.txt").write_text(result.stderr, encoding="utf-8")
            if result.returncode:
                raise RuntimeError(f"CUDA probe failed: {result.stderr or result.stdout}")
            report = json.loads(result.stdout)
            (run_dir / f"{name}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            if index:
                reports.append(report)
                telemetry.append({"phase": f"after-{name}", **gpu_snapshot()})
            else:
                # Keep nvidia-smi polling outside all formally timed runs.
                stop.set()
                thread.join(timeout=20)
                if thread.is_alive():
                    raise RuntimeError("Warmup telemetry did not finish before measurement")
                telemetry.append({"phase": "before-timed-runs", **gpu_snapshot()})
    finally:
        stop.set()
        thread.join(timeout=20)
        (run_dir / "telemetry.json").write_text(json.dumps(telemetry, indent=2) + "\n", encoding="utf-8")
    after = gpu_snapshot()
    for key in ("uuid", "driver_version", "power.limit"):
        if before[key] != after[key]:
            raise ValueError(f"Comparison setting changed during measurement: {key}")
    summary = {}
    for mode in reports[0]["modes"]:
        rates = [x for report in reports for x in report["modes"][mode]["rates_Top_per_s"]]
        summary[mode] = {"median_Top_per_s": statistics.median(rates), "min_Top_per_s": min(rates),
                         "max_Top_per_s": max(rates), "measurements": len(rates),
                         "all_cells_matched": all(r["modes"][mode]["mismatches"] == 0 for r in reports)}
        if baseline:
            summary[mode]["ratio_to_baseline"] = (
                summary[mode]["median_Top_per_s"] / baseline["summary"][mode]["median_Top_per_s"])
    final = {"label": args.label, "unlock_state_verified_by_benchmark": False,
        "utc": datetime.now(timezone.utc).isoformat(), "settings": settings,
        "before": before, "after": after, "summary": summary, "run_directory": str(run_dir),
        "telemetry_samples": len(telemetry), "note": "Register-resident issue rate; all-one arithmetic only. "
        "Normal Windows desktop applications remain running. Not an application or general accuracy test."}
    path = run_dir / "report.json"
    path.write_text(json.dumps(final, indent=2) + "\n", encoding="utf-8")
    if args.label == "baseline":
        (OUTPUT / "latest-baseline.json").write_text(json.dumps({"report": str(path)}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(path), "summary": summary}, indent=2), flush=True)


if __name__ == "__main__":
    main()
