import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

with open(r'c:\CODE\Dispatch\Opus_pro_research\data\research\best-caption-strategy-coaching-self-improvement.md', encoding='utf-8') as fh:
    text = fh.read()
    print("=== Coaching Research Body ===")
    print(text[1000:3500])
