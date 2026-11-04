import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

for p in [
    r'c:\CODE\Dispatch\Opus_pro_research\data\mcp\mcp.md',
    r'c:\CODE\Dispatch\Opus_pro_research\data\core_features\api.md',
    r'c:\CODE\Dispatch\Opus_pro_research\data\agent\mcp.md'
]:
    with open(p, encoding='utf-8') as f:
        print(f"=== {p} ===")
        print(f.read())
        print("="*60)
