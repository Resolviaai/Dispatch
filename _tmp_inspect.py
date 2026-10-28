import os

import httpx
from dotenv import load_dotenv

load_dotenv()
tok = os.getenv("SUPABASE_ACCESS_TOKEN")


def q(sql):
    r = httpx.post(
        "https://api.supabase.com/v1/projects/aldzlbdxplqkcnyjhzmm/database/query",
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
        json={"query": sql}, timeout=60.0)
    if r.status_code >= 400:
        print("ERR", r.status_code, r.text[:300])
        return None
    return r.json()


print(q(
    "select id::text, local_clip_id, window_start, window_end, "
    "window_start - 415.45 ds, window_end - 318.04 de1, window_end - 423.415873 de2, "
    "window_start::float8 = 415.45::float8 eq415, window_end::float8 = 318.04::float8 eq318 "
    "from public.edit_windows where local_clip_id in "
    "('clip_20261027_014714_7cc712','clip_20261027_014714_c6c9cb') order by created_at"))

print("settings:", q("select name, setting from pg_settings where name in ('extra_float_digits','server_version')"))
