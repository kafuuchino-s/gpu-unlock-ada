#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prepare private, exact-board port inputs from the reviewed 2705 capture.

This performs no GPU operations and produces no bootable application. Original
capture completeness/permissions are preserved, including filesystem failures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import uuid

from read_probe import read_limited, report_fields, summarize

ROOT = Path(__file__).resolve().parents[1]
PROM_SHA = "5de4c2a277b58775801b468bdddfeae48c3ea426f653b69a1905c61224143a83"
CONFIG_SHA = "98dec987f6da0a475fb6bdb366c3da872f9ad604328e866166eb3cde7df45d6d"
BOOT_SHA = "d1f7e351ef06e6442bde49c11bd3120f2a3b6ee9bee9be28347745b3ca02b486"
ESP_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
DATA_TYPE = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"
REGISTER_FIELDS = {
    0: "chip", 0x118128: "gfw_plm", 0x118234: "gfw_boot",
    0x110100: "gfw_cpuctl", 0x1FA824: "wpr2_lo", 0x1FA828: "wpr2_hi",
    0x1FA7CC: "wpr_cfg_plm", 0x1FA7C4: "wpr_plm", 0x9A0148: "fbpa_plm",
    0x823804: "feat_plm", 0x823814: "reg_00823814", 0x823818: "reg_00823818",
    0x1183A4: "fb_mib", 0x625F04: "reg_00625f04", 0x1FA7C8: "reg_001fa7c8",
    0x1FA82C: "reg_001fa82c", 0x1FA830: "reg_001fa830",
    0x824148: "reg_00824148", 0x8241E0: "reg_008241e0",
}
DRIVER_SHA = {
    "console": "9629abe4d4ac34606081f67ae586350c507788b69f14cc4d4c14307c1e1454cc",
    "graphics": "09ac95c96ac63973e73e806c9d6fda7501785b33f12155cff4bdb76f9272ad88",
    "nvgop": "216c7270fa08ad9e4c3694d34d825e9e904554a1af5ffa2aeead4317c5537b37",
}
FWSEC_FIELDS = {
    "image_off": 285512, "image_size": 66816, "imem_pa": 0, "imem_size": 63232,
    "imem_va": 0, "dmem_pa": 0, "data_off": 63232, "dmem_size": 3456,
    "pkc_off": 2852, "interface_off": 28, "ucode_id": 9, "engine_mask": 1024,
    "sig_count": 2, "sig_versions": 3, "sig_off": 284744, "sig_bytes": 768,
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalized_guid(value: str) -> str:
    return str(uuid.UUID(value.strip("{}")))


def hard_drive_path(path: str) -> dict[str, object]:
    data = bytes.fromhex(path)
    offset = 0
    partition = None
    while offset + 4 <= len(data):
        kind, subtype, size = struct.unpack_from("<BBH", data, offset)
        if size < 4 or offset + size > len(data):
            raise ValueError("Invalid partition device path")
        if kind == 4 and subtype == 1:
            if size != 42 or partition is not None:
                raise ValueError("Ambiguous partition device path")
            number, start, sectors = struct.unpack_from("<IQQ", data, offset + 4)
            if data[offset + 40:offset + 42] != b"\x02\x02":
                raise ValueError("Expected GPT partition signature")
            partition = {"guid": str(uuid.UUID(bytes_le=data[offset + 24:offset + 40])),
                         "number": number, "offset": start * 512, "size": sectors * 512}
        if kind == 0x7F:
            if subtype != 0xFF or size != 4 or offset + size != len(data):
                raise ValueError("Invalid partition path terminator")
            if partition is None:
                raise ValueError("Partition path has no hard-drive node")
            return partition
        offset += size
    raise ValueError("Partition path is not terminated")


def review_boot(context: dict, review: dict) -> dict:
    """Resolve only the observed non-system NTFS OpenVolume failure."""
    boot = context["windows_boot"]
    if boot["enumeration_status"] != "0x0000000000000000" \
            or boot["candidate_count"] != 1 or boot["saved_count"] != 1:
        raise ValueError("Windows boot enumeration/candidate count changed")
    candidate = boot["candidates"][0]
    part = hard_drive_path(candidate["path"])
    expected = {"number": 1, "offset": 1048576, "size": 314572800}
    if {k: v for k, v in part.items() if k != "guid"} != expected or candidate.get("esp_type_confirmed") is not True \
            or candidate["file"] != "BOOTMGFW00.BIN" or candidate["bytes"] != 1604016 \
            or candidate["sha256"] != BOOT_SHA or candidate["capture_status"] != "0x0000000000000000":
        raise ValueError("Captured system ESP/boot file differs from reviewed inputs")
    errors = boot["filesystem_errors"]
    if len(errors) != 1 or errors[0]["stage"] != "open_volume" \
            or errors[0]["status"] != "0x800000000000000e" or boot["complete"] is not False:
        raise ValueError("Filesystem failure differs from the individually reviewed case")
    failed = hard_drive_path(errors[0]["path"])
    partitions = {normalized_guid(p["guid"]): p for p in review["partitions"]}
    if len(partitions) != len(review["partitions"]) or set(partitions) != {part["guid"], failed["guid"]} \
            or part["guid"] == failed["guid"]:
        raise ValueError("Windows partition crosscheck is missing or duplicated")
    esp, data = partitions[part["guid"]], partitions[failed["guid"]]
    if esp["is_system"] is not True or esp["partition_number"] != 1 \
            or normalized_guid(esp["gpt_type"]) != ESP_TYPE \
            or esp["offset"] != part["offset"] or esp["size"] != part["size"]:
        raise ValueError("Captured ESP is not the Windows-confirmed system partition")
    if {k: v for k, v in failed.items() if k != "guid"} != {"number": 5, "offset": 967775879168, "size": 55594786304} \
            or data["is_system"] is not False or data["is_boot"] is not False \
            or normalized_guid(data["gpt_type"]) != DATA_TYPE or data["filesystem"] != "NTFS" \
            or data["partition_number"] != failed["number"] or data["offset"] != failed["offset"] \
            or data["size"] != failed["size"] or data["disk_number"] != esp["disk_number"]:
        raise ValueError("Failed volume is not the reviewed non-system data partition")
    if review["windows_boot_file"] != {"bytes": 1604016, "sha256": BOOT_SHA}:
        raise ValueError("Windows-installed boot manager does not match the captured ESP file")
    return {"system_partition": part, "boot_bytes": 1604016, "boot_sha256": BOOT_SHA,
            "reviewed_unreadable_data_partition": failed,
            "raw_enumeration_complete": False,
            "review_reason": "The sole OpenVolume failure is an independently identified non-system NTFS data partition; the system ESP and boot manager were read and hash-verified."}


def prepare(run: Path, partition_review: Path, fwsec_path: Path, output: Path) -> dict:
    summary = summarize(run, None)
    fields = summary["observations"]
    if fields["probe_version"] != 3 or summary["cold_state_observed"] is not True \
            or summary["windows_transaction_registers_complete"] is not True \
            or summary["pci_config_sha256"] != CONFIG_SHA or summary["prom"]["sha256"] != PROM_SHA \
            or fields["subsystem_id"] != 0x27056688:
        raise ValueError("Requires the reviewed complete 2705/6688:2705 cold GPU capture")
    context = json.loads(read_limited(run / "CONTEXT.JSON", 8 * 1024 * 1024))
    review = json.loads(partition_review.read_text(encoding="utf-8-sig"))
    boot = review_boot(context, review)
    expected_errors = [f"Windows filesystem {context['windows_boot']['filesystem_errors'][0]['filesystem_index']}: open_volume (0x800000000000000e)",
                       "Windows boot-file enumeration is incomplete"]
    if summary["capture_errors"] != expected_errors or context["platform"]["complete"] is not True:
        raise ValueError("Additional capture failures require independent review")
    fwsec = json.loads(fwsec_path.read_text(encoding="utf-8-sig"))
    prom = read_limited(run / "PROM.BIN", 1048576)
    fw_image = prom[285512:285512 + 66816]
    if fwsec.get("parser_result") != 0 or fwsec.get("physical_prom_sha256") != PROM_SHA \
            or fwsec.get("fwsec") != FWSEC_FIELDS \
            or digest(fw_image) != "764c243179d68a2e507b5e29169049daec1438c59e451a5c039d4ada708aaaff":
        raise ValueError("FWSEC analysis differs from the reviewed physical PROM")
    graph = context["platform"]
    handles = {h["id"]: h for h in graph["handles"]}
    agents = graph["gpu_agents"]
    files = {"PROM.BIN": prom, "PCI-CONFIG.BIN": read_limited(run / "PCI-CONFIG.BIN", 256),
             "BOOTMGFW00.BIN": read_limited(run / "BOOTMGFW00.BIN", 2 * 1024 * 1024)}
    if digest(files["BOOTMGFW00.BIN"]) != BOOT_SHA:
        raise ValueError("Captured boot-manager bytes changed")
    drivers = {}
    for role, expected_sha in DRIVER_SHA.items():
        matching = [a for a in agents if a["code"]["loaded_sha256"] == expected_sha]
        if len(matching) != 1:
            raise ValueError(f"Missing or duplicate reviewed {role} driver")
        driver = matching[0]
        code = bytes.fromhex(driver["code"]["hex"])
        if digest(code) != expected_sha:
            raise ValueError("Driver code checksum changed")
        files[f"driver-{role}.bin"] = code
        drivers[role] = {k: v for k, v in driver.items() if k != "code"}
        drivers[role]["code"] = {k: v for k, v in driver["code"].items() if k != "hex"}
    profile = {
        "kind": "reviewed_private_port_inputs", "profile_inputs_usable": True,
        "unlock_enabled": False, "release_authorized": False, "hardware_unlock_tested": False,
        "source_run": str(run.resolve()), "raw_capture_complete": summary["capture_complete"],
        "raw_capture_errors": summary["capture_errors"], "windows_boot_review": boot,
        "pci_id": "0x270510de", "pci_subsystem": "0x27056688", "bar0_base": fields["bar0_base"],
        "bar0_bytes": fields["bar0_length"], "fb_mib": fields["fb_mib"],
        "entry_ss0_observed": fields["ss0"], "entry_ss1_observed": fields["ss1"],
        "cold_registers": [{"offset": offset, "value": fields[name], "field": name}
                           for offset, name in REGISTER_FIELDS.items()],
        "prom_sha256": PROM_SHA, "fwsec": FWSEC_FIELDS, "drivers": drivers,
        "pci_path": handles[graph["target"]]["path"],
        "gpu_paths": [h["path"] for h in handles.values() if h["gpu_descendant"] and h["path"]],
        "capture_sha256": {name: digest(read_limited(run / name, 8 * 1024 * 1024))
                           for name in ("REPORT.TXT", "CONTEXT.JSON", "PROM.BIN", "PCI-CONFIG.BIN", "BOOTMGFW00.BIN", "PCI-ROM.BIN")},
        "private_files_sha256": {name: digest(data) for name, data in files.items()},
    }
    guid = uuid.UUID(boot["system_partition"]["guid"])
    initializer = "{0x%08x,0x%04x,0x%04x,{%s}}" % (
        guid.time_low, guid.time_mid, guid.time_hi_version,
        ",".join(f"0x{x:02x}" for x in guid.bytes[8:]))
    files["boot_profile.h"] = ("/* Private system ESP. Generated from reviewed RUN01; keep out of Git. */\n"
                                f"#define GU_WINDOWS_PARTITION_INITIALIZER {initializer}\n").encode("ascii")
    files["profile.json"] = (json.dumps(profile, indent=2) + "\n").encode("utf-8")
    output = output.resolve()
    if not output.is_relative_to((ROOT / "build/windows").resolve()) or output == run.resolve():
        raise ValueError("Private outputs must remain under build/windows, outside the raw capture")
    for name, data in files.items():
        path = output / name
        if path.exists() and (path.is_symlink() or path.read_bytes() != data):
            raise ValueError(f"Refusing to replace different private input: {path}")
    output.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        if not (output / name).exists():
            with (output / name).open("xb") as stream:
                stream.write(data)
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--partition-review", required=True, type=Path)
    parser.add_argument("--fwsec", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "build/windows/ad103-2705-profile")
    args = parser.parse_args()
    try:
        result = prepare(args.run, args.partition_review, args.fwsec, args.output)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"Profile rejected: {error}\n")
    print(json.dumps({"output": str(args.output), "profile_inputs_usable": result["profile_inputs_usable"],
                      "raw_capture_complete": result["raw_capture_complete"],
                      "release_authorized": False, "hardware_unlock_tested": False}, indent=2))


if __name__ == "__main__":
    main()
