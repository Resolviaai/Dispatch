import glob
import os
import re
from collections import Counter

agent_files = glob.glob(r'c:\CODE\Dispatch\Opus_pro_research\data\agent\*.md')
print(f"Total agent files: {len(agent_files)}")

categories = Counter()
models = []
use_cases = []
sample_workflows = []

for f in agent_files:
    fname = os.path.basename(f)
    if fname.startswith("models__"):
        models.append(fname)
    elif fname.startswith("use-case__"):
        use_cases.append(fname)
    elif fname.startswith("workflows__"):
        categories["workflows"] += 1
        if len(sample_workflows) < 30:
            sample_workflows.append(fname)
    else:
        prefix = fname.split('__')[0]
        categories[prefix] += 1

print("\nModels found:", len(models))
for m in sorted(models):
    print(" ", m)

print("\nUse Cases found:", len(use_cases))
for u in sorted(use_cases)[:15]:
    print(" ", u)

print("\nTotal Workflows:", categories["workflows"])
print("\nSample Workflows (first 15):")
for w in sample_workflows[:15]:
    print(" ", w)
