"""Generate complete Android mipmap icon set from master symbol."""
import os
import re
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
    <color name="ic_launcher_background">#004df7</color>
    <color name="primary_blue">#004df7</color>
</resources>
"""
(values_dir / "colors.xml").write_text(colors_xml, encoding="utf-8")

# Extract vector path from master white symbol
svg_text = Path("branding/master/dispatch-symbol-white.svg").read_text(encoding="utf-8")
m = re.search(r'd="([^"]+)"', svg_text)
if not m:
    raise ValueError("Could not extract symbol path data from dispatch-symbol-white.svg")
symbol_path = m.group(1)

# Adaptive vector drawable foreground in drawable/
# Central safe zone in 108dp: scale 0.65 to fit 72dp mask comfortably
drawable_dir = res_dir / "drawable"
drawable_dir.mkdir(parents=True, exist_ok=True)

fg_vector = f"""<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp"
    android:height="108dp"
    android:viewportWidth="256"
    android:viewportHeight="256">
    <group
        android:pivotX="128"
        android:pivotY="128"
        android:scaleX="0.65"
        android:scaleY="0.65">
        <path
            android:fillColor="#FFFFFF"
            android:pathData="{symbol_path}"
            android:fillType="evenOdd"/>
    </group>
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
