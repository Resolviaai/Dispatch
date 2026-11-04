import os
import re
import sys
import json
import time
import asyncio
import aiohttp
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import trafilatura
import html2text

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
METADATA_FILE = os.path.join(BASE_DIR, "metadata.jsonl")
URLS_FILE = os.path.join(BASE_DIR, "urls_to_fetch.txt")

h2t = html2text.HTML2Text()
h2t.ignore_links = False
h2t.ignore_images = True
h2t.body_width = 0

def categorize_url(url: str) -> tuple[str, str]:
    path = urlparse(url).path.strip('/')
    if not path or path == 'homepage' or path == 'home':
        return 'core_features', 'home'
    
    parts = path.split('/')
    top = parts[0]
    slug = "__".join(parts[1:]) if len(parts) > 1 else parts[0]
    # Clean filename
    slug = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', slug)
    
    core_pages = {
        'captions', 'ai-b-roll', 'ai-video-editor', 'calendar', 'team-workspace',
        'export-to-xml', 'ai-reframe', 'brand-templates', 'pricing', 'opussearch',
        'search-agent', 'trend-agent', 'monetization-toolkit', 'auto-cataloging',
        'clipanything', 'how-does-opus-clip-work', 'api', 'contact-api',
        'ai-motion-graphics', 'enhance-audio', 'ai-dubbing', 'mobile-app',
        'video-repurposing-ai', 'customer-stories', 'about', 'clip-for-cash',
        'clip-the-future', 'youtube-growth-playbook', 'growth-playbook'
    }
    
    if top in ['alternative', 'alternatives', 'capcut-alternative', 'descript-alternative', 'riverside-alternative']:
        return 'alternatives', f"{top}__{slug}" if top != slug else slug
    elif top in ['mcp']:
        return 'mcp', slug
    elif top in ['agent']:
        return 'agent', slug
    elif top in ['research']:
        return 'research', slug
    elif top in ['blog']:
        return 'blog', slug
    elif top in ['tools']:
        return 'tools', slug
    elif top in ['business', 'demo']:
        return top, slug
    elif top in core_pages:
        return 'core_features', slug
    else:
        return 'other', f"{top}__{slug}" if top != slug else slug

def extract_content(html: str, url: str) -> tuple[str, str, str]:
    soup = BeautifulSoup(html, 'html.parser')
    
    # Title
    title = soup.find('title')
    title_text = title.text.strip() if title else ""
    
    # Meta Description
    meta_desc = ""
    m = soup.find('meta', attrs={'name': 'description'}) or soup.find('meta', attrs={'property': 'og:description'})
    if m:
        meta_desc = m.get('content', '').strip()
        
    # Primary: trafilatura
    markdown_text = trafilatura.extract(html, include_links=True, include_images=False, output_format='markdown')
    
    # Fallback if trafilatura missed or gave too little
    if not markdown_text or len(markdown_text.strip()) < 150:
        main_content = soup.find('main') or soup.find('article') or soup.find('body')
        if main_content:
            markdown_text = h2t.handle(str(main_content))
        else:
            markdown_text = soup.get_text(separator='\n', strip=True)
            
    return title_text, meta_desc, markdown_text or ""

async def fetch_worker(session: aiohttp.ClientSession, queue: asyncio.Queue, meta_lock: asyncio.Lock, stats: dict):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    while True:
        try:
            url = await queue.get()
        except asyncio.CancelledError:
            break
            
        category, filename = categorize_url(url)
        target_dir = os.path.join(DATA_DIR, category)
        os.makedirs(target_dir, exist_ok=True)
        file_path = os.path.join(target_dir, f"{filename}.md")
        
        # Check if already fetched and valid
        if os.path.exists(file_path) and os.path.getsize(file_path) > 200:
            stats['skipped'] += 1
            queue.task_done()
            continue
            
        success = False
        for attempt in range(3):
            try:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        title, desc, md_body = extract_content(html, url)
                        
                        header = f"---\nurl: {url}\ntitle: {json.dumps(title)}\ndescription: {json.dumps(desc)}\ncategory: {category}\nword_count: {len(md_body.split())}\n---\n\n"
                        full_content = header + md_body
                        
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(full_content)
                            
                        entry = {
                            "url": url,
                            "title": title,
                            "description": desc,
                            "category": category,
                            "file": os.path.relpath(file_path, BASE_DIR),
                            "word_count": len(md_body.split()),
                            "status": "success"
                        }
                        async with meta_lock:
                            with open(METADATA_FILE, 'a', encoding='utf-8') as mf:
                                mf.write(json.dumps(entry) + '\n')
                                
                        stats['success'] += 1
                        success = True
                        break
                    elif resp.status in (404, 410):
                        stats['failed'] += 1
                        break
                    else:
                        await asyncio.sleep(1.5 * (attempt + 1))
            except Exception as e:
                if attempt == 2:
                    stats['failed'] += 1
                await asyncio.sleep(1.5 * (attempt + 1))
                
        queue.task_done()
        total_done = stats['success'] + stats['skipped'] + stats['failed']
        if total_done % 50 == 0 or total_done == stats['total']:
            print(f"[{total_done}/{stats['total']}] Success: {stats['success']} | Skipped: {stats['skipped']} | Failed: {stats['failed']}", flush=True)

async def main():
    if not os.path.exists(URLS_FILE):
        print(f"URLs file not found: {URLS_FILE}")
        return

    with open(URLS_FILE, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip()]

    print(f"Starting fetch for {len(urls)} URLs...")
    queue = asyncio.Queue()
    for u in urls:
        queue.put_nowait(u)

    stats = {
        'total': len(urls),
        'success': 0,
        'skipped': 0,
        'failed': 0
    }
    meta_lock = asyncio.Lock()

    # Use TCPConnector with limit to be polite
    connector = aiohttp.TCPConnector(limit=15, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for _ in range(12):  # 12 concurrent workers
            t = asyncio.create_task(fetch_worker(session, queue, meta_lock, stats))
            tasks.append(t)

        await queue.join()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    print(f"\nFinished! Total: {stats['total']} | Success: {stats['success']} | Skipped: {stats['skipped']} | Failed: {stats['failed']}")

if __name__ == "__main__":
    asyncio.run(main())
