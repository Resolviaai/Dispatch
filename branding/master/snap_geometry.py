"""Calculate and write exact 30-degree snapped geometry for master SVGs."""
import math
from pathlib import Path

# Snapped master symbol
# Outer bounds: x: 44 to 212 (width 168), y: 44 to 212 (height 168)
# Left corners: r = 16
# Inner triangle: base x = 96, tip x = 160.
# dx = 64, dy = 64 * tan(30 deg) = 36.9504 -> y1 = 91.05, y2 = 164.95
# Tip at (160, 128)

def make_symbol_svg(outer_r=16, corner_r=6, name="dispatch-symbol.svg", title="Dispatch Symbol"):
    # Precise 30 degree play triangle
    # We construct the triangle path with small rounded corners if corner_r > 0
    # Or clean polygon
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256" role="img" aria-labelledby="title-sym">
  <title id="title-sym">{title}</title>
  <path fill="#070A0F" fill-rule="evenodd" d="M 60 44 L 136 44 C 178 44 212 78 212 128 C 212 178 178 212 136 212 L 60 212 A 16 16 0 0 1 44 196 L 44 60 A 16 16 0 0 1 60 44 Z M 96 91.05 L 160 128 L 96 164.95 Z"/>
</svg>"""
    return svg

def make_symbol_small_svg():
    # Small cut: outer margins 38 (size 180x180), base x = 92, tip x = 164 (dx = 72)
    # dy = 72 * tan(30) = 41.5692 -> y1 = 86.43, y2 = 169.57
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256" role="img" aria-labelledby="title-sym-small">
  <title id="title-sym-small">Dispatch Symbol (Small-size cut)</title>
  <path fill="#070A0F" fill-rule="evenodd" d="M 58 38 L 138 38 C 182 38 218 74 218 128 C 218 182 182 218 138 218 L 58 218 A 20 20 0 0 1 38 198 L 38 58 A 20 20 0 0 1 58 38 Z M 92 86.43 L 164 128 L 92 169.57 Z"/>
</svg>"""
    return svg

Path("branding/master/dispatch-symbol.svg").write_text(make_symbol_svg(), encoding="utf-8")
Path("branding/master/dispatch-symbol-small.svg").write_text(make_symbol_small_svg(), encoding="utf-8")
print("Updated master symbols with exact 30.0 degree geometry")
