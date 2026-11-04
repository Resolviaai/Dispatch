import glob
import os

files = glob.glob(r'c:\CODE\Dispatch\Opus_pro_research\data\mcp\*.md')
print(f'Total MCP files: {len(files)}')
for f in sorted(files):
    name = os.path.basename(f)
    with open(f, encoding='utf-8') as fh:
        text = fh.read()
        lines = [l for l in text.splitlines() if l.startswith('title:')]
        title = lines[0].replace('title:', '').strip().strip('"') if lines else ''
        words = len(text.split())
        print(f'{name:60} | {words:5} words | {title[:60]}')
