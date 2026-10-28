"""Read-only product audit probes. Writes only an isolated temporary database.
Run from repository root: .venv/bin/python docs/audits/omega-2026-10-28/reproduce.py
Assertions describe CURRENT DEFECTS, not desired acceptance behavior.
No live credentials, publishing or network calls are used.
"""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sandbox = tempfile.TemporaryDirectory(prefix='dispatch-omega-')
os.environ.update(DISPATCH_STORAGE_DIR=sandbox.name, DISPATCH_DB_PATH=sandbox.name+'/database/audit.db', DISPATCH_DISABLE_AUTO_OUTBOX='1', DISPATCH_DISABLE_BACKGROUND_POLLERS='1')
import asyncio
import json
from unittest.mock import patch, Mock
from starlette.requests import Request
from dispatch import db
import importlib
web = importlib.import_module('dispatch.web.app')
from dispatch.cloud import sync
from dispatch.ai_clips.worker import HighlightWorker

def request(host='192.168.1.77'):
    return Request({'type':'http','method':'POST','path':'/api/settings','headers':[(b'host',b'192.168.1.10:8000')], 'scheme':'http','query_string':b'', 'client':(host,1234),'server':('192.168.1.10',8000)})

def response(data):
    r=Mock(ok=True,status_code=200,text='OK'); r.json.return_value=data; return r

db.init_db()
with db.get_db_connection() as c:
    c.execute("INSERT INTO clips(id,start_time,end_time,duration,title,user_id) VALUES('audit_clip',0,20,20,'original','owner-A')")
    c.execute("INSERT INTO publishing_outbox(id,clip_id,user_id,platform,status,idempotency_key) VALUES('audit_job','audit_clip','owner-A','youtube','reconciliation_required','audit_key')")

# 1: LAN address alone passes mutation auth.
web.verify_dashboard_auth(request())
print('01 CONFIRMED: unauthenticated LAN request passes verify_dashboard_auth')

# 2: authenticated B may mutate A reconciliation job (auth itself controlled).
with patch.object(web,'verify_dashboard_auth'), patch.object(web,'get_authenticated_user',return_value={'user_id':'owner-B'}):
    asyncio.run(web.resolve_reconciliation_job('audit_job',web.ResolveReconciliationRequest(action='mark_failed'),request('203.0.113.5')))
with db.get_db_connection() as c:
    assert c.execute("SELECT status FROM publishing_outbox WHERE id='audit_job'").fetchone()[0]=='failed'
print('02 CONFIRMED: reconciliation endpoint has no job-owner gate; owner-B marks owner-A job failed')

# 3: range revision is NOT an edit revision. Both stale editors save successfully.
assert db.save_clip_caption_data('audit_clip',json.dumps({'editor':'A'}),expected_range_revision=0)
assert db.save_clip_caption_data('audit_clip',json.dumps({'editor':'stale-B'}),expected_range_revision=0)
assert json.loads(db.get_clip_caption_data('audit_clip'))['editor']=='stale-B'
print('03 CONFIRMED: two caption saves at identical range revision silently overwrite')

# 4: stranded inbox state is never consumed after restart.
db.register_youtube_video('audit_video',title='audit')
db.update_youtube_video('audit_video',status='ANALYZING')
with patch.object(HighlightWorker,'process_video_highlights') as work:
    HighlightWorker().process_pending()
    assert work.call_count==0
assert db.get_youtube_video('audit_video')['status']=='ANALYZING'
print('04 CONFIRMED: restarted HighlightWorker ignores persisted ANALYZING video')

# 5: ownerless clip assigned to most recently created profile.
env={'SUPABASE_URL':'https://audit.invalid','SUPABASE_SERVICE_ROLE_KEY':'test-not-secret','DISPATCH_WORKER_TOKEN':'test-not-secret'}
with patch.dict(os.environ,env), patch.object(sync.requests,'get',return_value=response([{'user_id':'newest-unrelated-user'}])):
    assert sync.get_default_user_id()=='newest-unrelated-user'
print('05 CONFIRMED: legacy owner fallback selects newest unrelated profile without consent')

# 6: modified render never restaged if any ready window exists; stale falsely reset.
f=Path(sandbox.name)/'changed.mp4'; f.write_bytes(b'new-render-placeholder')
clip={'id':'audit_clip','user_id':'owner-A','status':'ready_review','start_time':0,'end_time':20,'range_revision':2,'render_stale':1,'video_path':str(f)}
with patch.dict(os.environ,env), patch.object(sync.requests,'get',side_effect=[response([]),response([{'id':'old-window','media_object_id':'old-render'}])]), patch.object(sync.requests,'post',return_value=response([{'id':'cloud-clip'}])) as post:
    assert sync.sync_clip_to_cloud('audit_clip',clip)
    assert post.call_count==1
    assert post.call_args.kwargs['json']['render_stale'] is False
print('06 CONFIRMED: ready old cloud window prevents changed render upload; stale flag forced false')

# 7: downstream cloud link rejection still returns success.
with patch.dict(os.environ,env), patch.object(sync.requests,'get',side_effect=[response([]),response([])]), patch.object(sync.requests,'put',return_value=response({})), patch.object(sync.requests,'patch',return_value=response({})), patch.object(sync.requests,'post',side_effect=[response([{'id':'cloud-clip'}]),response({'reservation_id':'r','upload_url':'https://audit.invalid/upload','media_object_id':'m'}),response({}),Mock(ok=False,status_code=500,text='database rejected edit_window')]):
    assert sync.sync_clip_to_cloud('audit_clip',clip) is True
print('07 CONFIRMED: edit_windows insertion HTTP 500 is reported as sync success')

# 8: absent encryption configuration still decrypts ciphertext under public fallback key.
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dispatch.youtube_connections import decrypt_refresh_token
import hashlib, base64
iv=b'123456789012'; key=hashlib.sha256(b'dispatch-fallback-vault-key').digest()
cipher=base64.urlsafe_b64encode(iv+AESGCM(key).encrypt(iv,b'not-a-real-token',None)).decode()
with patch.dict(os.environ,{'DISPATCH_TOKEN_VAULT_KEY':''}):
    assert decrypt_refresh_token(cipher)=='not-a-real-token'
print('08 CONFIRMED: PC vault decrypt accepts publicly known fallback key when unconfigured')
# 9: reconciliation finalizes using another connection before its job commit.
with db.get_db_connection() as c:
    c.execute("UPDATE clips SET status='approved' WHERE id='audit_clip'")
with patch.object(web,'verify_dashboard_auth'):
    asyncio.run(web.resolve_reconciliation_job('audit_job',web.ResolveReconciliationRequest(action='mark_published',remote_id='confirmed-manually'),request()))
assert db.get_clip_by_id('audit_clip')['status']=='approved'
with db.get_db_connection() as c:
    assert c.execute("SELECT status FROM publishing_outbox WHERE id='audit_job'").fetchone()[0]=='published'
print('09 CONFIRMED: manual reconciliation commits published job but leaves sole clip approved')

# 10: candidate worker and approval resolve different media coordinate systems.
first=Path(sandbox.name)/'part1.mp4'; first.write_bytes(b'placeholder-first')
second=Path(sandbox.name)/'part2.mp4'; second.write_bytes(b'placeholder-second')
with db.get_db_connection() as c:
    c.execute("INSERT INTO chunks(id,filename,filepath,duration) VALUES('part1','part1.mp4',?,600)",(str(first),))
clip2={'id':'multi','chunk_id':'part1','start_time':610,'end_time':630,'session_id':'multi-session'}
items=[{'duration':600,'local_video_path':str(first)},{'duration':600,'local_video_path':str(second)}]
worker_source=HighlightWorker()._resolve_clip_media_source(clip2,first,items)
assert worker_source[:3]==(second,10.0,30.0)
assert web.resolve_clip_source_path(clip2)==first
print('10 CONFIRMED: multi-segment candidate resolves part2[10:30]; approval resolver selects part1 with original [610:630]')

# 11: one tenant's editorial examples are supplied globally.
from dispatch.ai_clips.preference_learner import get_negative_feedback_prompt
with db.get_db_connection() as c:
    c.execute("INSERT INTO rejections(id,clip_id,title,transcript_snippet,reason) VALUES('r1','audit_clip','A private title','A private words','A private reason')")
assert 'A private words' in get_negative_feedback_prompt()
print('11 CONFIRMED: global negative-feedback prompt includes owner-A private excerpt without user scope')
sandbox.cleanup()
