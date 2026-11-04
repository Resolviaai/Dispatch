import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

for p in [
    r'c:\CODE\Dispatch\Opus_pro_research\data\blog\youtube-retention-graphs-explained.md',
    r'c:\CODE\Dispatch\Opus_pro_research\data\blog\youtube-shorts-hook-formulas.md',
    r'c:\CODE\Dispatch\Opus_pro_research\data\core_features\ai-b-roll.md'
]:
    with open(p, encoding='utf-8') as fh:
        print(f"=== {p} ===")
        print(fh.read()[:1200])
        print("="*60)
