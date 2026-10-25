# SPDX-License-Identifier: GPL-2.0-only
"""Validate offline UEFI captures without a GPU, EDK2 or NVIDIA firmware."""
from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import random
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("read_windows_probe", ROOT / "windows/read_probe.py")
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class WindowsProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.run_dir = Path(self.temporary.name)
        self.prom = random.Random(2705).randbytes(probe.PROM_BYTES)
        self.config = [0] * 64
        self.config[0] = 0x270510DE
        self.config[1] = 0x00100002
        self.config[2] = 0x030000A1
        self.config[11] = 0x27056688
        self.fields = {
            "probe_version": 1, "mode": "gpu_read_only", "unlock_implemented": 0,
            "probe_status": 0, "pci_id": self.config[0],
            "expected_subsystem_id": self.config[11], "subsystem_id": self.config[11],
            "pci_class_revision": self.config[2], "pci_command": 2,
            "segment": 0, "bus": 1, "device": 0, "function": 0,
            "bar0_base": 0xA00000000, "bar0_length": 0x1000000,
            "chip": 0x193000A1, "fb_mib": 0x3FF8, "ss0": 0, "ss1": 7,
            "wpr2_hi": 0, "gfw_plm": 0xFFFFFF8F, "gfw_boot": 0xFF,
            "gfw_cpuctl": 0x10, "feat_plm": 0xFFFFFF8F,
            "prom_bytes_completed": probe.PROM_BYTES, "prom_save_status": 0,
            "prom_bar_offset": 0x300000,
        }
        self.write_fixture()

    def write_fixture(self) -> None:
        self.write_report()
        (self.run_dir / "PCI-CONFIG.BIN").write_bytes(struct.pack("<64I", *self.config))
        (self.run_dir / "PROM.BIN").write_bytes(self.prom)

    def write_report(self) -> None:
        (self.run_dir / "REPORT.TXT").write_text("".join(
            f"{key}={hex(value) if isinstance(value, int) else value}\n"
            for key, value in self.fields.items()), encoding="ascii")

    @staticmethod
    def synthetic_rom() -> bytearray:
        rom = bytearray(512)
        rom[:2] = b"\x55\xaa"
        struct.pack_into("<H", rom, 0x18, 0x20)
        rom[0x20:0x24] = b"PCIR"
        struct.pack_into("<HH", rom, 0x24, 0x10DE, 0x2705)
        struct.pack_into("<H", rom, 0x30, 1)
        rom[0x34] = 3
        rom[0x35] = 0x80
        return rom

    def test_complete_capture_never_enables_unlock(self) -> None:
        result = probe.summarize(self.run_dir, None)
        self.assertTrue(result["capture_complete"])
        self.assertFalse(result["unlock_enabled"])
        self.assertFalse(result["hardware_unlock_tested"])
        self.assertEqual(result["subsystem"], "6688:2705")
        self.assertEqual(result["bdf"], "0000:01:00.0")
        self.assertTrue(result["cold_state_observed"])

    def test_failed_or_missing_completion_is_incomplete(self) -> None:
        for status in (0x8000000000000007, None):
            with self.subTest(status=status):
                if status is None:
                    self.fields.pop("probe_status", None)
                else:
                    self.fields["probe_status"] = status
                self.write_report()
                self.assertFalse(probe.summarize(self.run_dir, None)["capture_complete"])

    def test_wrong_device_and_subsystem_rejected(self) -> None:
        for index in (0, 11):
            with self.subTest(config_index=index):
                changed = self.config.copy()
                changed[index] ^= 1
                (self.run_dir / "PCI-CONFIG.BIN").write_bytes(struct.pack("<64I", *changed))
                with self.assertRaises(ValueError):
                    probe.summarize(self.run_dir, None)

    def test_truncated_and_blank_prom_rejected(self) -> None:
        for contents in (self.prom[:-1], bytes(probe.PROM_BYTES), b"\xff" * probe.PROM_BYTES):
            with self.subTest(bytes=len(contents), first=contents[:1]):
                (self.run_dir / "PROM.BIN").write_bytes(contents)
                with self.assertRaises(ValueError):
                    probe.summarize(self.run_dir, None)

    def test_missing_prom_preserves_diagnostics(self) -> None:
        (self.run_dir / "PROM.BIN").unlink()
        result = probe.summarize(self.run_dir, None)
        self.assertFalse(result["capture_complete"])
        self.assertIn("PROM capture is missing", result["capture_errors"])

    def test_duplicate_identity_rejected_but_progress_allowed(self) -> None:
        with self.assertRaises(ValueError):
            probe.report_fields(b"pci_id=0x270510de\npci_id=0x270410de\n")
        result = probe.report_fields(b"prom_bytes_completed=0x0\nprom_bytes_completed=0x100000\n")
        self.assertEqual(result["prom_bytes_completed"], probe.PROM_BYTES)

    def test_v2_repeated_stage_events_preserve_completion_checks(self) -> None:
        self.write_context()
        report = self.run_dir / "REPORT.TXT"
        with report.open("ab") as output:
            output.write(b"stage=collect_rom_and_registers\n"
                         b"stage=collect_uefi_and_windows_context\n")
        result = probe.summarize(self.run_dir, None)
        self.assertTrue(result["capture_complete"])
        self.assertEqual(result["observations"]["stage"], "collect_uefi_and_windows_context")
        with report.open("ab") as output:
            output.write(b"probe_status=0x0\n")
        with self.assertRaisesRegex(ValueError, "Duplicate report field: probe_status"):
            probe.summarize(self.run_dir, None)

    def test_pci_rom_bounds_and_termination(self) -> None:
        rom = self.synthetic_rom()
        self.assertEqual(probe.pci_rom_images(rom)[0]["pci_id"], "10de:2705")
        zero_length = bytearray(rom)
        struct.pack_into("<H", zero_length, 0x30, 0)
        unterminated = bytearray(rom)
        unterminated[0x35] = 0
        invalid_pointer = bytearray(rom)
        struct.pack_into("<H", invalid_pointer, 0x18, 0xFFF0)
        for damaged in (rom[:-1], zero_length, unterminated, invalid_pointer):
            with self.assertRaises(ValueError):
                probe.pci_rom_images(damaged)

    def test_prom_mapping_uses_actual_bytes(self) -> None:
        full = bytes(0x9400) + self.prom[:0xD6C00]
        views = probe.matching_views(self.prom, full)
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0]["full_rom_offset"], 0x9400)
        self.assertEqual(views[0]["bytes"], 0xD6C00)
        self.assertTrue(views[0]["covers_remaining_full_rom"])
        self.assertFalse(probe.matching_views(self.prom, b"\xff" * probe.PROM_BYTES))

    def write_context(self, version: int = 2) -> dict:
        self.fields.update(probe_version=version, build_id="a" * 64, context_save_status=0)
        if version == 3:
            self.fields.update({key: 0 for key in probe.TRANSACTION_REGISTERS})
        self.write_report()
        gpu_path = bytes.fromhex("0101060000017fff0400")
        usb_path = bytes.fromhex("0305060001007fff0400")
        disk_path = bytearray(bytes.fromhex("01010600000004012a00") + bytes(38) + bytes.fromhex("7fff0400"))
        struct.pack_into("<IQQ", disk_path, 10, 2, 2048, 204800)
        disk_path[46:48] = b"\x02\x02"
        boot = bytearray(512)
        boot[:2] = b"MZ"
        struct.pack_into("<I", boot, 60, 64)
        boot[64:68] = b"PE\0\0"
        struct.pack_into("<H", boot, 68, 0x8664)
        struct.pack_into("<H", boot, 84, 240)
        struct.pack_into("<H", boot, 88, 0x20B)
        struct.pack_into("<H", boot, 156, 10)
        (self.run_dir / "BOOTMGFW00.BIN").write_bytes(boot)
        code = bytes(range(16))
        context = {
            "context_version": 1, "probe_version": version, "build_id": "a" * 64,
            "gpu_writes_enabled": False, "release_authorized": False,
            "pci_id": "0x270510de", "subsystem_id": "0x27056688", "loading_path": usb_path.hex(),
            "platform": {"available": True, "complete": True, "atomic_snapshot": False,
                         "release_authorized": False, "target": 0,
                         "handles": [{"id": 0, "path": gpu_path.hex()}],
                         "gpu_agents": [{"agent": 0, "code": {
                             "rva": "0x00000200", "length": len(code), "relocations_in_code": 0,
                             "loaded_sha256": hashlib.sha256(code).hexdigest(), "hex": code.hex()}}]},
            "windows_boot": {"complete": True, "candidate_count": 1, "saved_count": 1,
                             "filesystem_count": 2, "filesystem_errors": [],
                             "candidates": [{"path": disk_path.hex(),
                                "partition": {"number": 2, "start_lba": 2048, "sectors": 204800,
                                              "signature": "00" * 16, "mbr_type": 2, "signature_type": 2},
                                "esp_type_confirmed": True, "capture_status": "0x0000000000000000",
                                "file": "BOOTMGFW00.BIN", "bytes": len(boot),
                                "sha256": hashlib.sha256(boot).hexdigest()}]},
        }
        self.save_context(context)
        return context

    def save_context(self, context: dict) -> None:
        (self.run_dir / "CONTEXT.JSON").write_text(json.dumps(context), encoding="ascii")

    def test_v2_complete_context_never_authorizes_release(self) -> None:
        self.write_context()
        result = probe.summarize(self.run_dir, None)
        self.assertTrue(result["capture_complete"])
        self.assertTrue(result["windows_handoff_capture_complete"])
        self.assertFalse(result["unlock_enabled"])
        self.assertFalse(result["uefi_windows_context"]["release_authorized"])
        self.assertEqual(len(result["uefi_windows_context"]["loaded_driver_codes"]), 1)

    def test_v2_missing_context_preserves_gpu_diagnostics(self) -> None:
        self.write_context()
        (self.run_dir / "CONTEXT.JSON").unlink()
        result = probe.summarize(self.run_dir, None)
        self.assertFalse(result["capture_complete"])
        self.assertEqual(result["device"], "10de:2705")
        self.assertIn("UEFI/Windows context is missing", result["capture_errors"])

    def test_v2_wrong_build_or_pci_identity_rejected(self) -> None:
        for key, value in (("build_id", "b" * 64), ("pci_id", "0x278210de"),
                           ("release_authorized", True)):
            with self.subTest(key=key):
                context = self.write_context()
                context[key] = value
                self.save_context(context)
                with self.assertRaises(ValueError):
                    probe.summarize(self.run_dir, None)

    def test_v2_driver_and_boot_file_corruption_rejected(self) -> None:
        context = self.write_context()
        context["platform"]["gpu_agents"][0]["code"]["hex"] = "ff" * 16
        self.save_context(context)
        with self.assertRaisesRegex(ValueError, "driver code size/hash"):
            probe.summarize(self.run_dir, None)
        self.write_context()
        (self.run_dir / "BOOTMGFW00.BIN").write_bytes(bytes(512))
        with self.assertRaisesRegex(ValueError, "boot file size/hash"):
            probe.summarize(self.run_dir, None)

    def test_v2_partial_graph_and_unconfirmed_esp_are_incomplete(self) -> None:
        context = self.write_context()
        context["platform"]["complete"] = False
        context["windows_boot"]["candidates"][0]["esp_type_confirmed"] = False
        self.save_context(context)
        result = probe.summarize(self.run_dir, None)
        self.assertFalse(result["capture_complete"])
        self.assertFalse(result["windows_handoff_capture_complete"])
        self.assertEqual(len(result["capture_errors"]), 2)

    def test_v2_path_traversal_duplicate_json_and_bad_path_rejected(self) -> None:
        context = self.write_context()
        context["windows_boot"]["candidates"][0]["file"] = "../BOOTMGFW00.BIN"
        self.save_context(context)
        with self.assertRaisesRegex(ValueError, "filename"):
            probe.summarize(self.run_dir, None)
        context = self.write_context()
        context["loading_path"] = "03050000"
        self.save_context(context)
        with self.assertRaisesRegex(ValueError, "device path"):
            probe.summarize(self.run_dir, None)
        (self.run_dir / "CONTEXT.JSON").write_text('{"context_version":1,"context_version":1}', encoding="ascii")
        with self.assertRaisesRegex(ValueError, "Duplicate context"):
            probe.summarize(self.run_dir, None)

    def test_v2_missing_code_or_multiple_boot_managers_need_review(self) -> None:
        context = self.write_context()
        context["platform"]["gpu_agents"][0]["code"]["hex"] = None
        candidate = context["windows_boot"]["candidates"][0].copy()
        candidate["file"] = "BOOTMGFW01.BIN"
        (self.run_dir / candidate["file"]).write_bytes((self.run_dir / "BOOTMGFW00.BIN").read_bytes())
        context["windows_boot"]["candidates"].append(candidate)
        context["windows_boot"].update(candidate_count=2, saved_count=2)
        self.save_context(context)
        result = probe.summarize(self.run_dir, None)
        self.assertFalse(result["capture_complete"])
        self.assertIn("Windows handoff needs one reviewed boot manager", result["capture_errors"])

    def test_v3_complete_registers_and_context_remain_read_only(self) -> None:
        self.write_context(3)
        result = probe.summarize(self.run_dir, None)
        self.assertTrue(result["capture_complete"])
        self.assertTrue(result["windows_transaction_registers_complete"])
        self.assertFalse(result["unlock_enabled"])
        self.assertFalse(result["uefi_windows_context"]["release_authorized"])

    def test_v3_missing_or_oversized_register_is_incomplete(self) -> None:
        for name in probe.TRANSACTION_REGISTERS:
            with self.subTest(register=name):
                self.write_context(3)
                del self.fields[name]
                self.write_report()
                result = probe.summarize(self.run_dir, None)
                self.assertFalse(result["capture_complete"])
                self.assertFalse(result["windows_transaction_registers_complete"])
        self.write_context(3)
        self.fields["wpr2_lo"] = 0x100000000
        self.write_report()
        self.assertFalse(probe.summarize(self.run_dir, None)["capture_complete"])

    def test_v3_filesystem_failure_preserves_boot_file_and_reason(self) -> None:
        context = self.write_context(3)
        failure = {"filesystem_index": 0, "stage": "open_volume",
                   "status": "0x8000000000000007", "path": None}
        context["windows_boot"].update(complete=False, filesystem_errors=[failure])
        self.save_context(context)
        result = probe.summarize(self.run_dir, None)
        self.assertFalse(result["capture_complete"])
        self.assertEqual(result["uefi_windows_context"]["filesystem_errors"], [failure])
        self.assertEqual(len(result["uefi_windows_context"]["windows_boot_files"]), 1)
        self.assertIn("Windows filesystem 0: open_volume (0x8000000000000007)", result["capture_errors"])

    def test_v3_invalid_filesystem_diagnostics_or_version_rejected(self) -> None:
        failure = {"filesystem_index": 0, "stage": "device_path",
                   "status": "0x8000000000000003", "path": None}
        for change in ({"filesystem_index": -1}, {"status": "0x0000000000000000"},
                       {"path": "03050000"}, {"stage": "unknown"}, {}):
            with self.subTest(change=change):
                context = self.write_context(3)
                context["windows_boot"]["filesystem_errors"] = [failure | change]
                context["windows_boot"]["complete"] = not bool(change)
                self.save_context(context)
                with self.assertRaises(ValueError):
                    probe.summarize(self.run_dir, None)
        context = self.write_context(3)
        context["probe_version"] = 2
        self.save_context(context)
        with self.assertRaisesRegex(ValueError, "version or permissions"):
            probe.summarize(self.run_dir, None)

    def test_full_rom_for_other_device_rejected(self) -> None:
        rom = self.synthetic_rom()
        struct.pack_into("<H", rom, 0x26, 0x2704)
        path = self.run_dir / "other-board.rom"
        path.write_bytes(rom)
        with self.assertRaises(ValueError):
            probe.summarize(self.run_dir, path)


if __name__ == "__main__":
    unittest.main()
