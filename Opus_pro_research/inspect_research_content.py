import glob, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

targets = [
    'best-caption-strategy-coaching-self-improvement.md',
    'best-caption-strategy-gaming.md',
    'broll-retention.md',
    'engagement-rate.md',
    'video-retention.md'
]

# Find matches or close matches
all_files = glob.glob(r'c:\CODE\Dispatch\Opus_pro_research\data\research\*.md')
for t in targets:
    matched = [f for f in all_files if t.replace('.md', '') in f]
    if matched:
        print(f"=== {os.path.basename(matched[0])} ===")
        with open(matched[0], encoding='utf-8') as fh:
            print(fh.read()[:1500])
        print("="*60)
