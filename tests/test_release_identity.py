"""Release-identity drift guard: the trusted signer fingerprint must be identical
in the release script AND the Android updater constant (when implemented)."""
import re
import unittest
from pathlib import Path

EXPECTED = "94f778490fde8ffd2c7e644d0c8169249f2e79829d769b5f71aa0301c5643804"


class TestReleaseIdentity(unittest.TestCase):
    def test_release_script_pins_expected_fingerprint(self):
        s = Path("scripts/prepare_release.ps1").read_text(encoding="utf-8")
        self.assertIn(f'"{EXPECTED}"', s, "prepare_release.ps1 must enforce the trusted fingerprint")

    def test_update_manager_matches_script_fingerprint(self):
        mgr = Path("android/app/src/main/java/com/resolvia/dispatch/update/DispatchUpdateManager.kt")
        if not mgr.exists():
            self.skipTest("DispatchUpdateManager not implemented yet")
        self.assertIn(EXPECTED, mgr.read_text(encoding="utf-8"))

    def test_update_json_schema_matches_script_fields(self):
        s = Path("scripts/prepare_release.ps1").read_text(encoding="utf-8")
        for field in ("schemaVersion", "packageName", "versionCode", "versionName",
                      "releaseTag", "releaseNotes", "apkAsset", "apkSizeBytes", "apkSha256"):
            self.assertIn(field, s, f"update.json field {field} missing from script")


if __name__ == "__main__":
    unittest.main()
