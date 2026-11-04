import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

for p in [
    r'c:\CODE\Dispatch\Opus_pro_research\data\mcp\inspiration__automatically-post-clips-based-on-whats-trending-right-now-powered-by-opusclip-perplexitys-api.md',
    r'c:\CODE\Dispatch\Opus_pro_research\data\mcp\inspiration__turn-any-longform-video-into-an-email-newsletter-using-the-opusclip-and-beehiiv-mcps.md',
    r'c:\CODE\Dispatch\Opus_pro_research\data\mcp\inspiration__get-your-best-unposted-clip-in-slack-every-morning.md'
]:
    with open(p, encoding='utf-8') as f:
        print(f"=== {p} ===")
        print(f.read()[:900])
        print("="*60)
