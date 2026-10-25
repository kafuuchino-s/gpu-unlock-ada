#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Inspect one read-only UEFI capture and write an unvalidated board profile."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
TARGET_ID = 0x270510DE
AD103_CHIP = 0x193000A1
PROM_BYTES = 1024 * 1024
TRANSACTION_REGISTERS = (
    "wpr_cfg_plm", "wpr_plm", "fbpa_plm", "wpr2_lo", "reg_00823814", "reg_00823818",
    "reg_00625f04", "reg_001fa7c8", "reg_001fa82c", "reg_001fa830", "reg_00824148",
    "reg_008241e0",
)


def read_limited(path: Path, maximum: int) -> bytes:
    with path.open("rb") as source:
        data = source.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError(f"{path.name} exceeds {maximum} bytes")
    return data


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def report_fields(data: bytes) -> dict[str, int | str]:
    fields: dict[str, int | str] = {}
    # Progress events repeat; identity and completion fields remain unique.
    repeatable = {"stage", "prom_bytes_completed", "target_gop_framebuffer", "target_gop_framebuffer_bytes",
                  "target_gop_width", "target_gop_height"}
    for line in data.decode("ascii").splitlines():
        match = re.fullmatch(r"([a-z][a-z0-9_]*)=([a-z0-9_]+)", line)
        if not match:
            continue
        key, value = match.groups()
        if key in fields and key not in repeatable:
            raise ValueError(f"Duplicate report field: {key}")
        fields[key] = int(value, 16 if value.startswith("0x") else 10) \
            if value.startswith("0x") or value.isdecimal() else value
    return fields


def pci_rom_images(data: bytes) -> list[dict[str, object]]:
    images = []
    offset = 0
    for _ in range(32):
        if offset + 0x1A > len(data) or data[offset:offset + 2] != b"\x55\xaa":
            break
        pcir = offset + struct.unpack_from("<H", data, offset + 0x18)[0]
        if pcir < offset + 0x1A or pcir + 0x16 > len(data) or data[pcir:pcir + 4] != b"PCIR":
            raise ValueError("Malformed PCI ROM image header")
        length = struct.unpack_from("<H", data, pcir + 0x10)[0] * 512
        if length < 512 or offset + length > len(data) or pcir + 0x16 > offset + length:
            raise ValueError("PCI ROM image length is out of bounds")
        vendor, device = struct.unpack_from("<HH", data, pcir + 4)
        last = bool(data[pcir + 0x15] & 0x80)
        images.append({"offset": offset, "bytes": length,
                       "pci_id": f"{vendor:04x}:{device:04x}", "code_type": data[pcir + 0x14],
                       "checksum_zero": sum(data[offset:offset + length]) % 256 == 0,
                       "last_image": last})
        offset += length
        if last:
            return images
    raise ValueError("PCI ROM has no complete, terminated image chain")


def matching_views(prom: bytes, rom: bytes) -> list[dict[str, object]]:
    """Find exact PROM-prefix slices in an independently exported full ROM."""
    prefix = prom[:4096]
    if not prefix or len(set(prefix)) == 1:
        return []
    views = []
    offset = 0
    for _ in range(32):
        offset = rom.find(prefix, offset)
        if offset < 0:
            break
        maximum = min(len(prom), len(rom) - offset)
        count = next((index for index in range(maximum)
                      if prom[index] != rom[offset + index]), maximum)
        if count >= 65536:
            views.append({"prom_offset": 0, "full_rom_offset": offset,
                          "bytes": count, "sha256": sha256(prom[:count]),
                          "covers_remaining_full_rom": count == len(rom) - offset})
        offset += 1
    return views


def context_profile(run: Path, fields: dict[str, int | str]) -> dict[str, object]:
    """Validate private platform data without granting any release/write permission."""
    errors: list[str] = []
    if not (run / "CONTEXT.JSON").is_file():
        return {"available": False, "complete": False,
                "errors": ["UEFI/Windows context is missing"]}

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate context JSON field: {key}")
            result[key] = value
        return result

    def hex_bytes(value: object, maximum: int) -> bytes:
        if not isinstance(value, str) or len(value) > maximum * 2 or len(value) % 2 \
                or not re.fullmatch(r"[0-9a-f]*", value):
            raise ValueError("Invalid bounded context hex data")
        return bytes.fromhex(value)

    def path_data(value: object) -> bytes:
        data = hex_bytes(value, 4096)
        offset = 0
        while offset + 4 <= len(data):
            kind, subtype, size = struct.unpack_from("<BBH", data, offset)
            if size < 4 or size > len(data) - offset:
                break
            if kind == 0x7F:
                if subtype == 0xFF and size == 4 and offset + size == len(data):
                    return data
                break
            offset += size
        raise ValueError("Malformed or multiple-instance context device path")

    data = read_limited(run / "CONTEXT.JSON", 8 * PROM_BYTES)
    context = json.loads(data, object_pairs_hook=unique_pairs)
    if not isinstance(context, dict) or context.get("context_version") != 1 \
            or context.get("probe_version") not in (2, 3) \
            or context.get("probe_version") != fields.get("probe_version") \
            or context.get("gpu_writes_enabled") is not False \
            or context.get("release_authorized") is not False:
        raise ValueError("Unexpected context version or permissions")
    build_id = context.get("build_id")
    if not isinstance(build_id, str) or not re.fullmatch(r"[0-9a-f]{64}", build_id) \
            or build_id != fields.get("build_id"):
        raise ValueError("Context build ID does not match the report")
    if context.get("pci_id") != "0x270510de" \
            or context.get("subsystem_id") != f"0x{fields.get('subsystem_id', 0):08x}":
        raise ValueError("Context PCI identity does not match the report")
    if fields.get("context_save_status") != 0:
        errors.append("Context save did not complete successfully")
    if context.get("loading_path") is None:
        errors.append("Loading USB path is missing")
    else:
        path_data(context["loading_path"])
    graph = context.get("platform")
    if not isinstance(graph, dict):
        raise ValueError("Missing UEFI platform context")
    if graph.get("available") is not True or graph.get("complete") is not True:
        errors.append("UEFI ownership snapshot is incomplete")
    if graph.get("available") is True and (graph.get("release_authorized") is not False
                                           or graph.get("atomic_snapshot") is not False):
        raise ValueError("Unexpected ownership snapshot permissions")
    handles = graph.get("handles", [])
    agents = graph.get("gpu_agents", [])
    if not isinstance(handles, list) or len(handles) > 4096 or not isinstance(agents, list) or len(agents) > 32:
        raise ValueError("Invalid ownership snapshot bounds")
    handle_ids = set()
    for entry in handles:
        if not isinstance(entry, dict) or type(entry.get("id")) is not int \
                or entry["id"] < 0 or entry["id"] in handle_ids:
            raise ValueError("Invalid or duplicate platform handle ID")
        handle_ids.add(entry["id"])
        if entry.get("path") is not None:
            path_data(entry["path"])
    if graph.get("target") not in handle_ids:
        errors.append("Target GPU is missing from the ownership graph")
    driver_codes = []
    for agent in agents:
        if not isinstance(agent, dict):
            raise ValueError("Invalid GPU driver entry")
        code = agent.get("code")
        if not isinstance(code, dict):
            errors.append("GPU driver code view is unavailable")
            continue
        if code.get("hex") is None:
            errors.append("GPU driver code bytes were not captured")
            continue
        raw = hex_bytes(code["hex"], 512 * 1024)
        if not raw or len(raw) != code.get("length") or sha256(raw) != code.get("loaded_sha256"):
            raise ValueError("Loaded GPU driver code size/hash mismatch")
        driver_codes.append({"agent": agent.get("agent"), "rva": code.get("rva"),
                             "bytes": len(raw), "sha256": sha256(raw),
                             "relocations_in_code": code.get("relocations_in_code")})
    if not driver_codes:
        errors.append("No loaded GPU driver code was captured")
    windows = context.get("windows_boot")
    if not isinstance(windows, dict) or not isinstance(windows.get("candidates"), list) \
            or len(windows["candidates"]) > 16:
        raise ValueError("Invalid Windows boot context")
    filesystem_errors = windows.get("filesystem_errors", [])
    if fields.get("probe_version") == 3:
        count = windows.get("filesystem_count")
        if type(count) is not int or count < 0 or not isinstance(filesystem_errors, list) \
                or len(filesystem_errors) > 128 or "filesystem_errors" not in windows:
            raise ValueError("Invalid Windows filesystem diagnostics")
        observed_indices = set()
        stages = {"device_path", "filesystem_protocol", "open_volume", "close_volume",
                  "candidate_limit", "partition_path", "boot_file", "close_file"}
        for failure in filesystem_errors:
            if not isinstance(failure, dict):
                raise ValueError("Invalid Windows filesystem failure")
            index, stage, status = failure.get("filesystem_index"), failure.get("stage"), failure.get("status")
            if type(index) is not int or not 0 <= index < min(count, 128) \
                    or index in observed_indices or stage not in stages \
                    or not isinstance(status, str) or not re.fullmatch(r"0x[0-9a-f]{16}", status) \
                    or int(status, 16) == 0:
                raise ValueError("Invalid Windows filesystem failure details")
            observed_indices.add(index)
            if failure.get("path") is not None:
                path_data(failure["path"])
            errors.append(f"Windows filesystem {index}: {stage} ({status})")
        if filesystem_errors and windows.get("complete") is True:
            raise ValueError("Windows filesystem failures contradict completion")
    candidates = windows["candidates"]
    if len(candidates) != windows.get("candidate_count"):
        raise ValueError("Windows boot candidate count mismatch")
    if windows.get("complete") is not True:
        errors.append("Windows boot-file enumeration is incomplete")
    boot_files = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError("Invalid Windows boot candidate")
        path = path_data(candidate.get("path"))
        observed_partition = None
        offset = 0
        while offset + 4 < len(path):
            kind, subtype, size = struct.unpack_from("<BBH", path, offset)
            if kind == 4 and subtype == 1 and size == 42:
                number, start, sectors = struct.unpack_from("<IQQ", path, offset + 4)
                observed_partition = {"number": number, "start_lba": start, "sectors": sectors,
                                      "signature": path[offset + 24:offset + 40].hex(),
                                      "mbr_type": path[offset + 40], "signature_type": path[offset + 41]}
                break
            offset += size
        if candidate.get("capture_status") != "0x0000000000000000":
            errors.append(f"Windows boot file {index} was not captured")
            continue
        filename = f"BOOTMGFW{index:02d}.BIN"
        if candidate.get("file") != filename:
            raise ValueError("Unexpected Windows boot capture filename")
        contents = read_limited(run / filename, 8 * PROM_BYTES)
        if len(contents) != candidate.get("bytes") or sha256(contents) != candidate.get("sha256"):
            raise ValueError("Windows boot file size/hash mismatch")
        if len(contents) < 64 or contents[:2] != b"MZ":
            raise ValueError("Windows boot capture is not a PE file")
        pe = struct.unpack_from("<I", contents, 60)[0]
        if pe > len(contents) - 96 or contents[pe:pe + 4] != b"PE\0\0" \
                or struct.unpack_from("<H", contents, pe + 4)[0] != 0x8664 \
                or struct.unpack_from("<H", contents, pe + 24)[0] != 0x20B \
                or struct.unpack_from("<H", contents, pe + 24 + 68)[0] != 10:
            raise ValueError("Windows boot capture must be AMD64 PE32+ EFI_APPLICATION")
        partition = candidate.get("partition")
        if not isinstance(partition, dict) or any(type(partition.get(key)) is not int
                for key in ("number", "start_lba", "sectors", "mbr_type", "signature_type")):
            raise ValueError("Windows boot partition metadata is incomplete")
        if partition != observed_partition or partition["number"] <= 0 \
                or partition["start_lba"] <= 0 or partition["sectors"] <= 0:
            raise ValueError("Windows partition metadata does not match its device path")
        if candidate.get("esp_type_confirmed") is not True:
            errors.append("Windows boot partition type needs review")
        boot_files.append({"file": filename, "bytes": len(contents), "sha256": sha256(contents),
                           "partition": partition, "device_path": candidate["path"]})
    if len(boot_files) != windows.get("saved_count"):
        raise ValueError("Windows boot saved-file count mismatch")
    if len(boot_files) != 1:
        errors.append("Windows handoff needs one reviewed boot manager")
    return {"available": True, "sha256": sha256(data), "build_id": build_id,
            "complete": not errors, "errors": errors, "loaded_driver_codes": driver_codes,
            "windows_boot_files": boot_files, "filesystem_errors": filesystem_errors,
            "release_authorized": False}


def summarize(run: Path, full_rom: Path | None) -> dict[str, object]:
    fields = report_fields(read_limited(run / "REPORT.TXT", 16384))
    if fields.get("probe_version") not in (1, 2, 3) or fields.get("mode") != "gpu_read_only":
        raise ValueError("Not a version 1/2/3 GPU read-only probe report")
    if fields.get("unlock_implemented") != 0:
        raise ValueError("Unexpected probe mode")
    result: dict[str, object] = {
        "kind": "unvalidated_board_profile", "unlock_enabled": False,
        "hardware_unlock_tested": False, "source_run": str(run.resolve()),
        "capture_status": fields.get("probe_status", "incomplete"), "observations": fields,
    }
    errors = []
    if fields.get("probe_status") != 0:
        errors.append("Probe did not finish successfully; inspect REPORT.TXT")
    config_path = run / "PCI-CONFIG.BIN"
    config = None
    if config_path.is_file():
        data = read_limited(config_path, 256)
        if len(data) != 256:
            raise ValueError("PCI-CONFIG.BIN must contain exactly 256 bytes")
        config = struct.unpack("<64I", data)
        if config[0] != TARGET_ID or config[0] != fields.get("pci_id"):
            raise ValueError("PCI configuration does not match the RTX 4070 Ti SUPER report")
        if config[11] != fields.get("subsystem_id") or config[11] != fields.get("expected_subsystem_id"):
            raise ValueError("PCI subsystem does not match the report")
        if config[2] >> 8 != 0x030000 or config[2] != fields.get("pci_class_revision"):
            raise ValueError("PCI display class/revision does not match the report")
        if config[1] & 0xFFFF != fields.get("pci_command"):
            raise ValueError("PCI command does not match the report")
        result["pci_config_sha256"] = sha256(data)
    else:
        errors.append("PCI configuration snapshot is missing")
    required_numbers = ("segment", "bus", "device", "function", "bar0_base", "bar0_length",
                        "chip", "fb_mib", "ss0", "ss1", "wpr2_hi", "gfw_plm", "gfw_boot",
                        "gfw_cpuctl", "feat_plm")
    transaction_registers_complete = all(type(fields.get(key)) is int
            and 0 <= fields[key] <= 0xFFFFFFFF for key in TRANSACTION_REGISTERS)
    result["windows_transaction_registers_complete"] = transaction_registers_complete
    if fields.get("probe_version") == 3:
        required_numbers += TRANSACTION_REGISTERS
    for key in required_numbers:
        if not isinstance(fields.get(key), int):
            errors.append(f"Missing numeric observation: {key}")
    if fields.get("probe_version") == 3 and not transaction_registers_complete:
        errors.append("Windows transaction register baseline is incomplete or out of range")
    if fields.get("chip") != AD103_CHIP:
        errors.append("AD103 chip identity was not confirmed")
    if not errors and config is not None:
        result["device"] = "10de:2705"
        result["subsystem"] = f"{config[11] & 0xffff:04x}:{config[11] >> 16:04x}"
        result["bdf"] = (f"{fields['segment']:04x}:{fields['bus']:02x}:"
                         f"{fields['device']:02x}.{fields['function']:x}")
        result["chip"] = f"{fields['chip']:08x}"
        result["bar0"] = f"{fields['bar0_base']:x}"
        result["bar0_bytes"] = fields["bar0_length"]
        result["observed_fb_mib_register_value"] = fields["fb_mib"]
        result["cold_state_observed"] = (
            fields["wpr2_hi"] >> 4 == 0 and fields["feat_plm"] == 0xFFFFFF8F
            and fields["ss0"] & 0x88888888 == 0 and fields["ss1"] == 7
            and fields["gfw_plm"] & 1 == 1 and fields["gfw_boot"] & 0xFF == 0xFF
            and fields["gfw_cpuctl"] & 0x10 == 0x10)
    prom = None
    if (run / "PROM.BIN").is_file():
        prom = read_limited(run / "PROM.BIN", PROM_BYTES)
        if len(prom) != PROM_BYTES or fields.get("prom_bytes_completed") != PROM_BYTES:
            raise ValueError("PROM capture is incomplete")
        if fields.get("prom_save_status") != 0 or fields.get("prom_bar_offset") != 0x300000:
            raise ValueError("PROM read/save metadata is inconsistent")
        if len(set(prom)) <= 1:
            raise ValueError("PROM capture is blank")
        result["prom"] = {"bytes": len(prom), "sha256": sha256(prom), "bar_offset": "0x300000"}
    else:
        errors.append("PROM capture is missing")
    if (run / "PCI-ROM.BIN").is_file():
        data = read_limited(run / "PCI-ROM.BIN", 2 * PROM_BYTES)
        if len(data) != fields.get("pci_rom_bytes") or fields.get("pci_rom_save_status") != 0:
            raise ValueError("PCI ROM capture metadata is inconsistent")
        result["uefi_pci_rom"] = {"bytes": len(data), "sha256": sha256(data),
                                   "images": pci_rom_images(data)}
    if full_rom:
        data = read_limited(full_rom, 2 * PROM_BYTES)
        images = pci_rom_images(data)
        if not any(image["pci_id"] == "10de:2705" for image in images):
            raise ValueError("Full ROM does not identify an RTX 4070 Ti SUPER")
        result["full_rom"] = {"bytes": len(data), "sha256": sha256(data), "images": images}
        result["prom_full_rom_view_candidates"] = matching_views(prom, data) if prom else []
    context = context_profile(run, fields) if fields.get("probe_version") in (2, 3) else {
        "available": False, "complete": False, "errors": ["Version 1 did not collect UEFI/Windows context"]}
    result["uefi_windows_context"] = context
    result["windows_handoff_capture_complete"] = context["complete"]
    if fields.get("probe_version") in (2, 3):
        errors.extend(context["errors"])
    result["capture_errors"] = errors
    result["capture_complete"] = not errors
    result["next_step"] = (
        "Review the original report and ROM mapping, then implement the board-specific unlock/handoff. "
        "This profile does not authorize or enable GPU writes.")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="USB GPU-UNLOCK-2705/RUNxx directory")
    parser.add_argument("--rom", type=Path, help="Optional full VBIOS exported with GPU-Z")
    parser.add_argument("--output", type=Path, default=ROOT / "build/windows/board-profile.json")
    args = parser.parse_args()
    try:
        result = summarize(args.run, args.rom)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError, struct.error) as error:
        parser.exit(2, f"Capture rejected: {error}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not result["capture_complete"]:
        parser.exit(2, "Capture is incomplete. The JSON preserves available diagnostics.\n")


if __name__ == "__main__":
    main()
