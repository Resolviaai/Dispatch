import os
import json
import re
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi

videos = [
    'Gfsd8NNuD9g',
    'GGg61sdEjeI',
    '2TlIg3VokY8',
    'HE4rLEQpiXY',
    'AH_ugxmLeUM',
    'wLJ40GV2XEc',
    'Xzh8xjimmp8',
    'EcbgbKtOELY',
    'ld1zhQMXxXU',
    '86PGRyQjdzQ'
]

base_dir = r'c:\CODE\Dispatch\design_research'
transcripts_dir = os.path.join(base_dir, 'transcripts')
os.makedirs(transcripts_dir, exist_ok=True)

api = YouTubeTranscriptApi()
metadata_list = []

for idx, vid in enumerate(videos, 1):
    url = f'https://www.youtube.com/watch?v={vid}'
    print(f'[{idx}/10] Processing {vid}...')
    meta = {'id': vid, 'url': url}
    try:
        ydl_opts = {'quiet': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            meta['title'] = info.get('title', 'Unknown Title')
            meta['uploader'] = info.get('uploader', 'Unknown Uploader')
            meta['duration'] = info.get('duration', 0)
            meta['description'] = info.get('description', '')[:500]
    except Exception as e:
        print(f'  yt_dlp warning for {vid}: {e}')
        meta['title'] = f'Video {vid}'
        meta['uploader'] = 'Unknown'
    
    clean_title = re.sub(r'[^\w\-_\. ]', '', meta['title'])[:40].strip().replace(' ', '_')
    meta['slug'] = clean_title

    # Fetch transcript
    try:
        t_data = api.fetch(vid)
        snippets = []
        full_text_lines = []
        for s in t_data:
            text = getattr(s, 'text', '') if hasattr(s, 'text') else s.get('text', '')
            start = getattr(s, 'start', 0.0) if hasattr(s, 'start') else s.get('start', 0.0)
            duration = getattr(s, 'duration', 0.0) if hasattr(s, 'duration') else s.get('duration', 0.0)
            snippets.append({'text': text, 'start': start, 'duration': duration})
            full_text_lines.append(f'[{start:06.2f}] {text}')
        
        meta['snippets_count'] = len(snippets)
        meta['total_words'] = sum(len(s['text'].split()) for s in snippets)
        
        # Save JSON
        json_path = os.path.join(transcripts_dir, f'{idx:02d}_{vid}_{clean_title}.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump({'meta': meta, 'snippets': snippets}, f, indent=2, ensure_ascii=False)
            
        # Save TXT
        txt_path = os.path.join(transcripts_dir, f'{idx:02d}_{vid}_{clean_title}.txt')
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(f"Title: {meta['title']}\nUploader: {meta['uploader']}\nURL: {url}\nDuration: {meta.get('duration', 0)}s\n\n")
            f.write('\n'.join(full_text_lines))
            
        print(f"  -> Transcripts saved! ({meta['snippets_count']} snippets, {meta['total_words']} words)")
    except Exception as e:
        print(f"  -> Transcript fetch error for {vid}: {e}")
        meta['error'] = str(e)
        
    metadata_list.append(meta)

with open(os.path.join(base_dir, 'metadata.json'), 'w', encoding='utf-8') as f:
    json.dump(metadata_list, f, indent=2, ensure_ascii=False)

print('Done fetching all transcripts!')
