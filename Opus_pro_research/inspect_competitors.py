import glob, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

targets = [
    'alternative__descript.md',
    'capcut-alternative.md',
    'riverside-alternative.md',
    'alternative__munch.md',
    'alternative__klap.md'
]

alt_files = glob.glob(r'c:\CODE\Dispatch\Opus_pro_research\data\alternatives\*.md')
for t in targets:
    matched = [f for f in alt_files if t.replace('.md', '') in f or os.path.basename(f) == t]
    if matched:
        print(f"=== {os.path.basename(matched[0])} ===")
        with open(matched[0], encoding='utf-8') as fh:
            print(fh.read()[:1200])
        print("="*60)
