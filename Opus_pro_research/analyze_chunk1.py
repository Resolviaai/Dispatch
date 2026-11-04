import glob
import os
import re

files = glob.glob(r'c:\CODE\Dispatch\Opus_pro_research\data\core_features\*.md')
print(f'Total core feature files: {len(files)}')
for f in sorted(files):
    name = os.path.basename(f)
    with open(f, encoding='utf-8') as fh:
        text = fh.read()
        lines = text.splitlines()
        title = ""
        for line in lines[:10]:
            if line.startswith("title:"):
                title = line.replace("title:", "").strip().strip('"')
                break
        words = len(text.split())
        print(f'{name:45} | {words:5} words | {title[:60]}')
