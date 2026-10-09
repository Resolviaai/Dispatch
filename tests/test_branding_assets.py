"""Tests verifying Dispatch logo assets, app icons, and web favicon endpoints."""
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from dispatch.web.app import app

class TestBrandingAssets(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_master_vector_files_exist(self):
        master_dir = Path("branding/master")
        self.assertTrue((master_dir / "dispatch-symbol.svg").exists(), "Master symbol missing")
        self.assertTrue((master_dir / "dispatch-symbol-small.svg").exists(), "Small-size cut missing")
        self.assertTrue((master_dir / "dispatch-lockup-horizontal.svg").exists(), "Horizontal lockup missing")

    def test_distribution_assets_exist(self):
        dist_dir = Path("branding/dist")
        required_dist = [
            "dispatch-symbol-black.svg",
            "dispatch-symbol-white.svg",
            "dispatch-symbol-mono-2563eb.svg",
            "dispatch-symbol-app-icon.svg",
            "dispatch-lockup-horizontal-black.svg",
            "dispatch-lockup-horizontal-white.svg",
            "favicon.ico",
            "favicon.svg",
            "apple-touch-icon.png",
            "site.webmanifest"
        ]
        for f in required_dist:
            self.assertTrue((dist_dir / f).exists(), f"Required distribution asset {f} missing")

    def test_android_app_icons_exist(self):
        res_dir = Path("android/app/src/main/res")
        densities = ["mipmap-mdpi", "mipmap-hdpi", "mipmap-xhdpi", "mipmap-xxhdpi", "mipmap-xxxhdpi"]
        for d in densities:
            self.assertTrue((res_dir / d / "ic_launcher.png").exists(), f"Missing ic_launcher.png in {d}")
            self.assertTrue((res_dir / d / "ic_launcher_round.png").exists(), f"Missing ic_launcher_round.png in {d}")
        
        # Adaptive vector drawable
        self.assertTrue((res_dir / "drawable" / "ic_launcher_foreground.xml").exists(), "Missing adaptive vector foreground")
        self.assertTrue((res_dir / "mipmap-anydpi-v26" / "ic_launcher.xml").exists(), "Missing adaptive icon XML")

    def test_web_static_and_favicon_endpoints(self):
        # /favicon.ico
        res_ico = self.client.get("/favicon.ico")
        self.assertEqual(res_ico.status_code, 200)
        self.assertEqual(res_ico.headers.get("content-type"), "image/x-icon")

        # /favicon.svg
        res_svg = self.client.get("/favicon.svg")
        self.assertEqual(res_svg.status_code, 200)
        self.assertEqual(res_svg.headers.get("content-type"), "image/svg+xml")

        # /apple-touch-icon.png
        res_apple = self.client.get("/apple-touch-icon.png")
        self.assertEqual(res_apple.status_code, 200)
        self.assertEqual(res_apple.headers.get("content-type"), "image/png")

        # /site.webmanifest
        res_manifest = self.client.get("/site.webmanifest")
        self.assertEqual(res_manifest.status_code, 200)
        self.assertEqual(res_manifest.headers.get("content-type"), "application/manifest+json")

    def test_dashboard_contains_velocity_logo(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.text
        self.assertIn("M 96 91.05 L 160 128 L 96 164.95 Z", html, "Velocity D 30-deg arrow path should be in dashboard")
        self.assertIn("/favicon.ico", html, "Favicon should be linked in dashboard HTML")

if __name__ == "__main__":
    unittest.main()
