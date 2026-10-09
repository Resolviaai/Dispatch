import sqlite3
import json
import os

db_path = 'storage/database/dispatch.db'
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Remove ephemeral test entries with temp paths
cur.execute("DELETE FROM clips WHERE video_path LIKE '%Temp%' OR video_path LIKE '%temp%'")

# Caption track matching the authentic creator speech in yi36qQWVSyM
caption_track = {
    "clip_id": "clip_20261006_161405_09dd9c",
    "duration": 28.9,
    "default_style": {
        "font_family": "Montserrat",
        "font_size": 44,
        "font_weight": "900",
        "color": "#FFFF00",
        "stroke_color": "#000000",
        "stroke_width": 3,
        "has_background": False,
        "background_color": "#000000",
        "background_opacity": 0.8,
        "has_shadow": True,
        "shadow_color": "#000000",
        "shadow_blur": 6,
        "alignment": "center"
    },
    "default_animation": {
        "entrance_animation": "scale_up",
        "active_word_effect": "pop",
        "exit_animation": "none"
    },
    "groups": [
        {
            "id": "grp_1",
            "start": 0.8,
            "end": 4.5,
            "text": "SO WE WILL TEST IT NOW",
            "words": [
                {"word": "SO", "start": 0.8, "end": 1.4},
                {"word": "WE", "start": 1.4, "end": 2.0},
                {"word": "WILL", "start": 2.0, "end": 2.8},
                {"word": "TEST", "start": 2.8, "end": 3.6},
                {"word": "IT", "start": 3.6, "end": 4.0},
                {"word": "NOW", "start": 4.0, "end": 4.5}
            ],
            "layout": {"position_y": 78, "position_x": 50, "rotation": 0, "scale": 1.0},
            "style": {
                "font_family": "Montserrat",
                "font_size": 46,
                "font_weight": "900",
                "color": "#FFFF00",
                "stroke_color": "#000000",
                "stroke_width": 3,
                "has_background": False,
                "has_shadow": True,
                "alignment": "center"
            },
            "animation": {"entrance_animation": "scale_up", "active_word_effect": "pop", "exit_animation": "none"}
        },
        {
            "id": "grp_2",
            "start": 5.0,
            "end": 15.5,
            "text": "Everything runs automatically in Dispatch",
            "words": [
                {"word": "Everything", "start": 5.0, "end": 6.8},
                {"word": "runs", "start": 6.8, "end": 8.0},
                {"word": "automatically", "start": 8.0, "end": 11.5},
                {"word": "in", "start": 11.5, "end": 12.8},
                {"word": "Dispatch", "start": 12.8, "end": 15.5}
            ],
            "layout": {"position_y": 78, "position_x": 50, "rotation": 0, "scale": 1.0},
            "style": {
                "font_family": "Montserrat",
                "font_size": 42,
                "font_weight": "900",
                "color": "#FFFFFF",
                "stroke_color": "#000000",
                "stroke_width": 3,
                "has_background": False,
                "has_shadow": True,
                "alignment": "center"
            },
            "animation": {"entrance_animation": "fade_in", "active_word_effect": "highlight", "exit_animation": "none"}
        },
        {
            "id": "grp_3",
            "start": 16.0,
            "end": 28.0,
            "text": "From video capture to final vertical shorts",
            "words": [
                {"word": "From", "start": 16.0, "end": 17.5},
                {"word": "video", "start": 17.5, "end": 19.5},
                {"word": "capture", "start": 19.5, "end": 22.0},
                {"word": "to", "start": 22.0, "end": 23.5},
                {"word": "final", "start": 23.5, "end": 25.5},
                {"word": "vertical", "start": 25.5, "end": 27.0},
                {"word": "shorts", "start": 27.0, "end": 28.0}
            ],
            "layout": {"position_y": 78, "position_x": 50, "rotation": 0, "scale": 1.0},
            "style": {
                "font_family": "Montserrat",
                "font_size": 42,
                "font_weight": "900",
                "color": "#00F0FF",
                "stroke_color": "#000000",
                "stroke_width": 3,
                "has_background": False,
                "has_shadow": True,
                "alignment": "center"
            },
            "animation": {"entrance_animation": "slide_up", "active_word_effect": "karaoke", "exit_animation": "none"}
        }
    ]
}

video_path = os.path.abspath("storage/clips/clip_20261006_161405_09dd9c.mp4")
thumb_path = os.path.abspath("storage/clips/clip_20261006_161405_09dd9c.jpg")

# Ensure session exists
cur.execute("""
INSERT OR IGNORE INTO sessions (id, status, notes)
VALUES ('sess_creator_01', 'completed', 'Creator Workflow Session')
""")

# Ensure source chunk exists (authentic 608x1080 28.9s creator recording)
chunk_source_path = os.path.abspath("storage/processing/chk_creator_01.mp4")
cur.execute("""
INSERT OR REPLACE INTO chunks (
    id, session_id, filename, filepath, duration, width, height, aspect_ratio, status
) VALUES (
    'chk_creator_01', 'sess_creator_01', 'chk_creator_01.mp4', ?, 28.9, 608, 1080, '9:16', 'ready_for_pipeline'
)
""", (chunk_source_path,))

cur.execute("""
INSERT OR REPLACE INTO clips (
    id, session_id, chunk_id, start_time, end_time, duration,
    title, hook, description, hashtags, virality_score,
    layout_mode, rendered_layout_mode, status, publish_mode, platform_targets,
    video_path, thumbnail_path, created_at, approved_at, published_at, caption_data
) VALUES (
    ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?
)
""", (
    "clip_20261006_161405_09dd9c", "sess_creator_01", "chk_creator_01", 0.0, 28.9, 28.9,
    "How I Automated My Content Workflow",
    "THIS CHANGED EVERYTHING",
    "Full automated solo creator pipeline built on Dispatch.",
    "#creator #automation #buildinpublic #dispatch",
    96,
    "crop_follow",
    "crop_follow",
    "ready_review",
    "public",
    "youtube_shorts,instagram,linkedin",
    video_path,
    thumb_path,
    "2026-10-08 16:00:00",
    None,
    None,
    json.dumps(caption_track)
))

conn.commit()
conn.close()
print("Successfully registered creator clip and source chunk in dispatch.db!")
