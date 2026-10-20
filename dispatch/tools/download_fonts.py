"""Font acquisition, validation, and manifest generation utility.
Downloads curated creator fonts and generates fonts_manifest.json.
"""
import os
import shutil
import urllib.request
from pathlib import Path
from fontTools.ttLib import TTFont

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
STORAGE_FONTS_DIR = ROOT_DIR / "storage" / "fonts"
STORAGE_FONTS_DIR.mkdir(parents=True, exist_ok=True)

# Curated font specifications
CURATED_FONTS = [
    {
        "family": "Anton",
        "category": "Bold / Captions",
        "weights": ["400"],
        "path": "ofl/anton/Anton-Regular.ttf",
        "filename": "Anton-Regular.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Anton"
    },
    {
        "family": "Archivo Black",
        "category": "Bold / Captions",
        "weights": ["400"],
        "path": "ofl/archivoblack/ArchivoBlack-Regular.ttf",
        "filename": "ArchivoBlack-Regular.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Archivo Black"
    },
    {
        "family": "Bebas Neue",
        "category": "Decorative",
        "weights": ["400"],
        "path": "ofl/bebasneue/BebasNeue-Regular.ttf",
        "filename": "BebasNeue-Regular.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Bebas Neue"
    },
    {
        "family": "Montserrat",
        "category": "Bold / Captions",
        "weights": ["400", "500", "600", "700", "800", "900"],
        "path": "ofl/montserrat/Montserrat[wght].ttf",
        "filename": "Montserrat-Variable.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Montserrat"
    },
    {
        "family": "Oswald",
        "category": "Bold / Captions",
        "weights": ["400", "500", "600", "700"],
        "path": "ofl/oswald/Oswald[wght].ttf",
        "filename": "Oswald-Variable.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Oswald"
    },
    {
        "family": "Barlow Condensed",
        "category": "Bold / Captions",
        "weights": ["400", "600", "700", "800", "900"],
        "path": "ofl/barlowcondensed/BarlowCondensed-Bold.ttf",
        "filename": "BarlowCondensed-Bold.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Barlow Condensed"
    },
    {
        "family": "League Spartan",
        "category": "Bold / Captions",
        "weights": ["400", "600", "700", "800", "900"],
        "path": "ofl/leaguespartan/LeagueSpartan[wght].ttf",
        "filename": "LeagueSpartan-Variable.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "League Spartan"
    },
    {
        "family": "Poppins",
        "category": "Clean",
        "weights": ["400", "500", "600", "700", "800", "900"],
        "path": "ofl/poppins/Poppins-Bold.ttf",
        "filename": "Poppins-Bold.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Poppins"
    },
    {
        "family": "Inter",
        "category": "Clean",
        "weights": ["400", "500", "600", "700", "800", "900"],
        "path": "ofl/inter/Inter[opsz,wght].ttf",
        "filename": "Inter-Variable.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Inter"
    },
    {
        "family": "Roboto",
        "category": "Clean",
        "weights": ["400", "500", "700", "900"],
        "path": "ofl/roboto/Roboto[wdth,wght].ttf",
        "filename": "Roboto-Variable.ttf",
        "license": "Apache License 2.0",
        "post_family": "Roboto"
    },
    {
        "family": "Open Sans",
        "category": "Clean",
        "weights": ["400", "600", "700", "800"],
        "path": "ofl/opensans/OpenSans[wdth,wght].ttf",
        "filename": "OpenSans-Variable.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Open Sans"
    },
    {
        "family": "Noto Sans",
        "category": "Clean",
        "weights": ["400", "600", "700", "800"],
        "path": "ofl/notosans/NotoSans[wdth,wght].ttf",
        "filename": "NotoSans-Variable.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Noto Sans"
    },
    {
        "family": "Space Grotesk",
        "category": "Clean",
        "weights": ["400", "500", "600", "700"],
        "path": "ofl/spacegrotesk/SpaceGrotesk[wght].ttf",
        "filename": "SpaceGrotesk-Variable.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Space Grotesk"
    },
    {
        "family": "Bangers",
        "category": "Gaming / Comedy",
        "weights": ["400"],
        "path": "ofl/bangers/Bangers-Regular.ttf",
        "filename": "Bangers-Regular.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Bangers"
    },
    {
        "family": "Luckiest Guy",
        "category": "Gaming / Comedy",
        "weights": ["400"],
        "path": "apache/luckiestguy/LuckiestGuy-Regular.ttf",
        "filename": "LuckiestGuy-Regular.ttf",
        "license": "Apache License 2.0",
        "post_family": "Luckiest Guy"
    },
    {
        "family": "Russo One",
        "category": "Gaming / Comedy",
        "weights": ["400"],
        "path": "ofl/russoone/RussoOne-Regular.ttf",
        "filename": "RussoOne-Regular.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Russo One"
    },
    {
        "family": "Titan One",
        "category": "Gaming / Comedy",
        "weights": ["400"],
        "path": "ofl/titanone/TitanOne-Regular.ttf",
        "filename": "TitanOne-Regular.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Titan One"
    },
    {
        "family": "Bungee",
        "category": "Gaming / Comedy",
        "weights": ["400"],
        "path": "ofl/bungee/Bungee-Regular.ttf",
        "filename": "Bungee-Regular.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Bungee"
    },
    {
        "family": "Permanent Marker",
        "category": "Decorative",
        "weights": ["400"],
        "path": "apache/permanentmarker/PermanentMarker-Regular.ttf",
        "filename": "PermanentMarker-Regular.ttf",
        "license": "Apache License 2.0",
        "post_family": "Permanent Marker"
    },
    {
        "family": "Noto Sans Devanagari",
        "category": "Language Support",
        "weights": ["400", "600", "700", "800"],
        "path": "ofl/notosansdevanagari/NotoSansDevanagari[wdth,wght].ttf",
        "filename": "NotoSansDevanagari-Variable.ttf",
        "license": "SIL Open Font License 1.1",
        "post_family": "Noto Sans Devanagari"
    }
]

def download_and_setup_fonts():
    base_cdn = "https://cdn.jsdelivr.net/gh/google/fonts@main/"
    manifest = []
    
    # 1. Download each curated open font
    for item in CURATED_FONTS:
        dest = STORAGE_FONTS_DIR / item["filename"]
        if not dest.exists() or dest.stat().st_size == 0:
            url = base_cdn + item["path"]
            print(f"Downloading {item['family']} from {url}...")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    content = r.read()
                    with open(dest, "wb") as f:
                        f.write(content)
                print(f"Saved {dest.name} ({len(content)} bytes)")
            except Exception as e:
                print(f"Warning: Failed to download {item['family']}: {e}")
        
        # Validate font file if present
        is_valid = False
        detected_family = item["family"]
        if dest.exists():
            try:
                tt = TTFont(str(dest))
                # read name record 1 or 4
                for record in tt["name"].names:
                    if record.nameID in (1, 4, 16):
                        try:
                            s = record.toUnicode()
                            if s and len(s) > 1:
                                detected_family = s
                                break
                        except Exception:
                            pass
                is_valid = True
            except Exception as e:
                print(f"Font validation error for {dest.name}: {e}")

        manifest.append({
            "family": item["family"],
            "category": item["category"],
            "weights": item["weights"],
            "filename": item["filename"],
            "license": item["license"],
            "is_valid": is_valid,
            "is_custom": False,
            "url": f"/api/fonts/{item['filename']}"
        })

    # 2. Check for Windows Impact font
    win_impact = Path("C:/Windows/Fonts/impact.ttf")
    if win_impact.exists():
        dest_impact = STORAGE_FONTS_DIR / "impact.ttf"
        if not dest_impact.exists():
            try:
                shutil.copy2(win_impact, dest_impact)
                print(f"Copied system impact.ttf to {dest_impact}")
            except Exception as e:
                print(f"Notice: Could not copy system impact font: {e}")
        manifest.insert(0, {
            "family": "Impact",
            "category": "Bold / Captions",
            "weights": ["400"],
            "filename": "impact.ttf",
            "license": "System / Core Web Font",
            "is_valid": True,
            "is_custom": False,
            "url": "/api/fonts/impact.ttf"
        })

    # 3. Write manifest.json
    import json
    manifest_path = STORAGE_FONTS_DIR / "fonts_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote font manifest with {len(manifest)} entries to {manifest_path}")

    # 4. Write license notices
    license_path = STORAGE_FONTS_DIR / "OFL_LICENSES.txt"
    with open(license_path, "w", encoding="utf-8") as f:
        f.write("DISPATCH CREATOR FONT LIBRARY LICENSES\n")
        f.write("======================================\n\n")
        for m in manifest:
            f.write(f"Family: {m['family']}\n")
            f.write(f"File: {m['filename']}\n")
            f.write(f"License: {m['license']}\n")
            f.write(f"Category: {m['category']}\n")
            f.write("Source: Google Fonts (SIL Open Font License 1.1 / Apache 2.0)\n\n")
    print(f"Wrote license documentation to {license_path}")

if __name__ == "__main__":
    download_and_setup_fonts()
