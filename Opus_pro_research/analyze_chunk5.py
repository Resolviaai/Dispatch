import glob, os, re
from collections import Counter

alt_files = glob.glob(r'c:\CODE\Dispatch\Opus_pro_research\data\alternatives\*.md')
tool_files = glob.glob(r'c:\CODE\Dispatch\Opus_pro_research\data\tools\*.md')

print(f"Total alternative files: {len(alt_files)}")
print(f"Total tool files: {len(tool_files)}")

print("\nCompetitors analyzed in alternatives/ (sample 20):")
for f in sorted(alt_files)[:20]:
    fname = os.path.basename(f)
    print(" ", fname)

# Categorize tools by function
tool_types = Counter()
for f in tool_files:
    fname = os.path.basename(f)
    parts = fname.split('-')
    prefix = parts[0]
    tool_types[prefix] += 1

print("\nTool prefixes (top 15):")
for k, v in tool_types.most_common(15):
    print(f"  {k}: {v}")
