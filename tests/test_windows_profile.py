# SPDX-License-Identifier: GPL-2.0-only
"""Reject broad exemptions for a partially captured Windows filesystem graph."""
import copy
from pathlib import Path
import struct
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "windows"))
import prepare_2705_profile as profile


def hd_path(number, offset, size, guid):
    node = struct.pack("<BBHIQQ16sBB", 4, 1, 42, number, offset // 512,
                       size // 512, uuid.UUID(guid).bytes_le, 2, 2)
    return (node + bytes((127, 255, 4, 0))).hex()


class WindowsProfileReviewTests(unittest.TestCase):
    def setUp(self):
        self.esp = "11111111-2222-4333-8444-555555555555"
        self.data = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        self.context = {"windows_boot": {
            "enumeration_status": "0x0000000000000000", "candidate_count": 1,
            "saved_count": 1, "complete": False,
            "candidates": [{"path": hd_path(1, 1048576, 314572800, self.esp),
                "esp_type_confirmed": True, "file": "BOOTMGFW00.BIN", "bytes": 1604016,
                "sha256": profile.BOOT_SHA, "capture_status": "0x0000000000000000"}],
            "filesystem_errors": [{"filesystem_index": 4, "stage": "open_volume",
                "status": "0x800000000000000e",
                "path": hd_path(5, 967775879168, 55594786304, self.data)}]}}
        self.review = {"partitions": [
            {"guid": self.esp, "is_system": True, "is_boot": False,
             "partition_number": 1, "gpt_type": profile.ESP_TYPE, "disk_number": 2,
             "offset": 1048576, "size": 314572800, "filesystem": "FAT32"},
            {"guid": self.data, "is_system": False, "is_boot": False,
             "partition_number": 5, "gpt_type": profile.DATA_TYPE, "disk_number": 2,
             "offset": 967775879168, "size": 55594786304, "filesystem": "NTFS"}],
             "windows_boot_file": {"bytes": 1604016, "sha256": profile.BOOT_SHA}}

    def test_review_preserves_raw_failure_and_grants_no_release(self):
        original = copy.deepcopy(self.context)
        result = profile.review_boot(self.context, self.review)
        self.assertEqual(self.context, original)
        self.assertFalse(result["raw_enumeration_complete"])
        self.assertNotIn("release_authorized", result)
        self.assertEqual(result["system_partition"]["guid"], self.esp)
        self.assertEqual(result["reviewed_unreadable_data_partition"]["guid"], self.data)

    def test_different_or_additional_failure_requires_review(self):
        for key, value in (("stage", "close_volume"), ("status", "0x8000000000000007")):
            with self.subTest(key=key):
                context = copy.deepcopy(self.context)
                context["windows_boot"]["filesystem_errors"][0][key] = value
                with self.assertRaises(ValueError):
                    profile.review_boot(context, self.review)
        for count in (0, 2):
            with self.subTest(count=count):
                context = copy.deepcopy(self.context)
                context["windows_boot"]["filesystem_errors"] *= count
                with self.assertRaises(ValueError):
                    profile.review_boot(context, self.review)

    def test_data_volume_must_be_independently_non_system_ntfs(self):
        for key, value in (("is_system", True), ("is_boot", True),
                           ("filesystem", "FAT32"), ("gpt_type", profile.ESP_TYPE),
                           ("disk_number", 3), ("offset", 967775879680),
                           ("size", 55594786816), ("guid", self.esp)):
            with self.subTest(key=key):
                review = copy.deepcopy(self.review)
                review["partitions"][1][key] = value
                with self.assertRaises(ValueError):
                    profile.review_boot(self.context, review)

    def test_esp_and_boot_manager_must_match_windows_crosscheck(self):
        for key, value in (("is_system", False), ("gpt_type", profile.DATA_TYPE),
                           ("partition_number", 2), ("size", 314573312)):
            with self.subTest(key=key):
                review = copy.deepcopy(self.review)
                review["partitions"][0][key] = value
                with self.assertRaises(ValueError):
                    profile.review_boot(self.context, review)
        for key, value in (("bytes", 1604017), ("sha256", "0" * 64)):
            with self.subTest(key=key):
                review = copy.deepcopy(self.review)
                review["windows_boot_file"][key] = value
                with self.assertRaises(ValueError):
                    profile.review_boot(self.context, review)

    def test_duplicate_or_unconfirmed_candidates_are_rejected(self):
        for key, value in (("candidate_count", 2), ("saved_count", 0), ("complete", True)):
            with self.subTest(key=key):
                context = copy.deepcopy(self.context)
                context["windows_boot"][key] = value
                with self.assertRaises(ValueError):
                    profile.review_boot(context, self.review)
        context = copy.deepcopy(self.context)
        context["windows_boot"]["candidates"][0]["esp_type_confirmed"] = False
        with self.assertRaises(ValueError):
            profile.review_boot(context, self.review)
        review = copy.deepcopy(self.review)
        review["partitions"].append(copy.deepcopy(review["partitions"][0]))
        with self.assertRaises(ValueError):
            profile.review_boot(self.context, review)

    def test_partition_paths_are_bounded_and_unambiguous(self):
        good = hd_path(1, 1048576, 314572800, self.esp)
        for bad in (good[:-2], good + "00", good[:-8] + good, "00000000"):
            with self.subTest(path=bad):
                with self.assertRaises(ValueError):
                    profile.hard_drive_path(bad)


if __name__ == "__main__":
    unittest.main()
