import glob, os, sys, io
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

tool_files = glob.glob(r'c:\CODE\Dispatch\Opus_pro_research\data\tools\*.md')
print(f"Total tools: {len(tool_files)}")

categories = defaultdict(list)
for f in tool_files:
    name = os.path.basename(f).replace('.md', '')
    if name.startswith('add-'):
        categories['Add Media & Effects (Audio/Voice/Subtitles/Overlays)'].append(name)
    elif 'resize' in name or 'aspect-ratio' in name or 'size' in name or '1080' in name or 'dimension' in name:
        categories['Aspect Ratio, Sizing & Cropping'].append(name)
    elif 'translate' in name or 'dub' in name or 'language' in name or 'subtitles' in name or 'caption' in name:
        categories['Translation, Dubbing & Captioning'].append(name)
    elif 'youtube' in name or 'tiktok' in name or 'instagram' in name or 'podcast' in name or 'linkedin' in name or 'twitter' in name:
        categories['Platform-Specific Clippers & Optimizers'].append(name)
    elif 'ai-' in name or 'generator' in name or 'maker' in name:
        categories['Generative & AI Creation Tools'].append(name)
    elif 'trim' in name or 'cut' in name or 'split' in name or 'remove' in name or 'compress' in name or 'convert' in name:
        categories['Video Utilities (Trim, Cut, Compress, Convert)'].append(name)
    else:
        categories['Other Specialized Tools'].append(name)

for cat, tools in sorted(categories.items(), key=lambda x: len(x[1]), reverse=True):
    print(f"\n### {cat} ({len(tools)} tools)")
    for t in sorted(tools)[:8]:
        print(f"  - {t}")
    if len(tools) > 8:
        print(f"  ... and {len(tools) - 8} more")
