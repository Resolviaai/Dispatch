"""Generate complete Android mipmap icon set from master symbol."""
import os
from pathlib import Path
from PIL import Image, ImageDraw

res_dir = Path("android/app/src/main/res")
densities = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}

# Source app icon PNG from branding/dist (512x512)
src_icon_path = Path("branding/dist/dispatch-symbol-app-icon-512.png")
if not src_icon_path.exists():
    raise FileNotFoundError(f"Missing {src_icon_path}")

base_img = Image.open(src_icon_path).convert("RGBA")

# Create circular mask for round icons
def make_round(img):
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, w, h), fill=255)
    round_img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    round_img.paste(img, (0, 0), mask=mask)
    return round_img

for folder_name, size in densities.items():
    folder = res_dir / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    
    # Square / Adaptive
    resized = base_img.resize((size, size), Image.Resampling.LANCZOS)
    resized.save(folder / "ic_launcher.png", format="PNG")
    
    # Round
    round_img = make_round(resized)
    round_img.save(folder / "ic_launcher_round.png", format="PNG")
    print(f"Generated {folder_name} ({size}x{size})")

# Also create values/colors.xml and mipmap-anydpi-v26 XMLs
values_dir = res_dir / "values"
values_dir.mkdir(parents=True, exist_ok=True)

colors_xml = """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="ic_launcher_background">#070A0F</color>
    <color name="primary_blue">#2563EB</color>
</resources>
"""
(values_dir / "colors.xml").write_text(colors_xml, encoding="utf-8")

# Adaptive vector drawable foreground in drawable/
drawable_dir = res_dir / "drawable"
drawable_dir.mkdir(parents=True, exist_ok=True)

fg_vector = """<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp"
    android:height="108dp"
    android:viewportWidth="256"
    android:viewportHeight="256">
    <path
        android:fillColor="#FFFFFF"
        android:pathData="M 60,44 L 136,44 C 178,44 212,78 212,128 C 212,178 178,212 136,212 L 60,212 A 16,16 0 0,1 44,196 L 44,60 A 16,16 0 0,1 60,44 Z M 96,91.05 L 160,128 L 96,164.95 Z"
        android:fillType="evenOdd"/>
</vector>
"""
(drawable_dir / "ic_launcher_foreground.xml").write_text(fg_vector, encoding="utf-8")

anydpi_dir = res_dir / "mipmap-anydpi-v26"
anydpi_dir.mkdir(parents=True, exist_ok=True)

adaptive_xml = """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background" />
    <foreground android:drawable="@drawable/ic_launcher_foreground" />
</adaptive-icon>
"""
(anydpi_dir / "ic_launcher.xml").write_text(adaptive_xml, encoding="utf-8")
(anydpi_dir / "ic_launcher_round.xml").write_text(adaptive_xml, encoding="utf-8")

print("All Android app icon resources created successfully!")
