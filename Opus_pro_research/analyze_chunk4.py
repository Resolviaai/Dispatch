import glob, os, json, re
from collections import Counter

research_files = glob.glob(r'c:\CODE\Dispatch\Opus_pro_research\data\research\*.md')
print(f"Total research files: {len(research_files)}")

# Group by category / title
categories = Counter()
stats_found = []

for f in research_files:
    fname = os.path.basename(f)
    prefix = fname.split('-')[0]
    categories[prefix] += 1
    with open(f, encoding='utf-8') as fh:
        text = fh.read()
        # Look for empirical numbers, e.g. "X clips", "%", "retention"
        matches = re.findall(r'(\d+(?:\.\d+)?%|\d+(?:\.\d+)?\s*(?:million|M)\s+clips|\b\d+\s*WPM\b)', text, re.IGNORECASE)
        if matches and len(stats_found) < 20:
            stats_found.append((fname, matches[:5]))

print("\nResearch file prefixes:")
for k, v in categories.most_common(10):
    print(f"  {k}: {v}")

print("\nSample Empirical Metrics Discovered:")
for fname, stats in stats_found[:12]:
    print(f"  {fname[:45]}: {stats}")
