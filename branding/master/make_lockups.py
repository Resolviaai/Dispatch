"""Build master lockups (horizontal and stacked) for Dispatch."""
from pathlib import Path

# Wordmark generator for DISPATCH
def get_wordmark_paths_horizontal(start_x=260):
    x = start_x
    paths = []
    # D (width 70)
    paths.append(
        f'<path d="M {x} 80 L {x+38} 80 C {x+58} 80 {x+70} 92 {x+70} 128 C {x+70} 164 {x+58} 176 {x+38} 176 L {x} 176 Z '
        f'M {x+18} 98 L {x+36} 98 C {x+46} 98 {x+52} 108 {x+52} 128 C {x+52} 148 {x+46} 158 {x+36} 158 L {x+18} 158 Z" fill-rule="evenodd" fill="#070A0F"/>'
    )
    x += 70 + 20
    # I (width 18)
    paths.append(f'<rect x="{x}" y="80" width="18" height="96" rx="2" fill="#070A0F"/>')
    x += 18 + 20
    # S (width 64)
    paths.append(
        f'<path d="M {x+58} 102 C {x+58} 88 {x+46} 80 {x+32} 80 C {x+16} 80 {x+6} 90 {x+6} 104 C {x+6} 118 {x+18} 124 {x+38} 129 C {x+54} 133 {x+64} 141 {x+64} 154 C {x+64} 168 {x+50} 176 {x+32} 176 C {x+14} 176 {x} 166 {x} 150 L {x+18} 150 C {x+18} 158 {x+24} 161 {x+32} 161 C {x+40} 161 {x+46} 157 {x+46} 152 C {x+46} 144 {x+38} 140 {x+22} 135 C {x+8} 130 {x} 120 {x} 106 C {x} 90 {x+14} 80 {x+32} 80 C {x+50} 80 {x+64} 90 {x+64} 102 Z" fill="#070A0F"/>'
    )
    x += 64 + 20
    # P (width 66)
    paths.append(
        f'<path d="M {x} 80 L {x+36} 80 C {x+54} 80 {x+66} 90 {x+66} 108 C {x+66} 126 {x+54} 136 {x+36} 136 L {x+18} 136 L {x+18} 176 L {x} 176 Z '
        f'M {x+18} 96 L {x+34} 96 C {x+44} 96 {x+48} 102 {x+48} 108 C {x+48} 114 {x+44} 120 {x+34} 120 L {x+18} 120 Z" fill-rule="evenodd" fill="#070A0F"/>'
    )
    x += 66 + 20
    # A (width 70)
    paths.append(
        f'<path d="M {x+26} 80 L {x+44} 80 L {x+70} 176 L {x+51} 176 L {x+44} 150 L {x+26} 150 L {x+19} 176 L {x} 176 Z '
        f'M {x+31} 102 L {x+39} 102 L {x+48} 135 L {x+22} 135 Z" fill-rule="evenodd" fill="#070A0F"/>'
    )
    x += 70 + 20
    # T (width 64)
    paths.append(
        f'<path d="M {x} 80 L {x+64} 80 L {x+64} 96 L {x+41} 96 L {x+41} 176 L {x+23} 176 L {x+23} 96 L {x} 96 Z" fill="#070A0F"/>'
    )
    x += 64 + 20
    # C (width 66)
    paths.append(
        f'<path d="M {x+60} 104 C {x+56} 90 {x+46} 80 {x+33} 80 C {x+14} 80 {x} 94 {x} 128 C {x} 162 {x+14} 176 {x+33} 176 C {x+46} 176 {x+56} 166 {x+60} 152 L {x+43} 152 C {x+40} 158 {x+36} 161 {x+33} 161 C {x+23} 161 {x+18} 150 {x+18} 128 C {x+18} 106 {x+23} 95 {x+33} 95 C {x+36} 95 {x+40} 98 {x+43} 104 Z" fill="#070A0F"/>'
    )
    x += 66 + 20
    # H (width 66)
    paths.append(
        f'<path d="M {x} 80 L {x+18} 80 L {x+18} 119 L {x+48} 119 L {x+48} 80 L {x+66} 80 L {x+66} 176 L {x+48} 176 L {x+48} 137 L {x+18} 137 L {x+18} 176 L {x} 176 Z" fill="#070A0F"/>'
    )
    x += 66
    return "\n  ".join(paths), x

sym_path = '<path fill="#070A0F" fill-rule="evenodd" d="M 60 44 L 136 44 C 178 44 212 78 212 128 C 212 178 178 212 136 212 L 60 212 A 16 16 0 0 1 44 196 L 44 60 A 16 16 0 0 1 60 44 Z M 96 91.05 L 160 128 L 96 164.95 Z"/>'

wm_h, end_x = get_wordmark_paths_horizontal(260)
view_w = end_x + 40 # 924

lockup_h_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {view_w} 256" width="{view_w}" height="256" role="img" aria-labelledby="title-lockup-h">
  <title id="title-lockup-h">Dispatch Logo Horizontal Lockup</title>
  <g id="symbol">
    {sym_path}
  </g>
  <g id="wordmark">
    {wm_h}
  </g>
</svg>"""

Path("branding/master/dispatch-lockup-horizontal.svg").write_text(lockup_h_svg, encoding="utf-8")
print("Wrote branding/master/dispatch-lockup-horizontal.svg")
