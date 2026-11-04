import glob, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

for m in ['models__sora.md', 'models__veo.md', 'models__kling-ai.md', 'models__hailuo-ai.md', 'use-case__ai-motion-graphics.md']:
    p = os.path.join(r'c:\CODE\Dispatch\Opus_pro_research\data\agent', m)
    if os.path.exists(p):
        print(f"=== {m} ===")
        with open(p, encoding='utf-8') as f:
            print(f.read()[:900])
        print("="*60)
