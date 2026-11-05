"""Unit tests for Waveform Analysis and Style-Aware Auto-Cut Engine."""

import math
import struct
import unittest
from pathlib import Path

from dispatch.video_engine.waveform import (
    compute_rms_profile,
    find_zero_crossing,
    snap_boundary_to_waveform_silence,
    is_waveform_interval_silent,
    detect_audio_clipping,
    detect_transient_clicks,
    detect_breaths_and_coughs,
)
from dispatch.video_engine.autocut import (
    detect_mechanical_stutters,
    detect_contextual_filler_words,
    analyze_waveform_and_transcript_autocuts,
)


def generate_synthetic_pcm(
    sample_rate: int = 16000,
    tone_duration: float = 1.0,
    silence_duration: float = 3.0,
    frequency: float = 440.0
) -> bytes:
    """Generate synthetic 16-bit mono PCM: tone -> silence -> tone."""
    samples = []
    
    # 1. Tone 1
    total_tone_samples = int(tone_duration * sample_rate)
    for i in range(total_tone_samples):
        # Apply exponential decay at end
        decay = 1.0
        if i > total_tone_samples - int(0.2 * sample_rate):
            decay = max(0.0, 1.0 - (i - (total_tone_samples - int(0.2 * sample_rate))) / (0.2 * sample_rate))
        val = int(16000 * decay * math.sin(2 * math.pi * frequency * (i / sample_rate)))
        samples.append(val)
        
    # 2. Silence
    total_silence_samples = int(silence_duration * sample_rate)
    for _ in range(total_silence_samples):
        samples.append(0)
        
    # 3. Tone 2
    for i in range(total_tone_samples):
        val = int(16000 * math.sin(2 * math.pi * frequency * (i / sample_rate)))
        samples.append(val)
        
    raw_bytes = struct.pack(f"<{len(samples)}h", *samples)
    return raw_bytes


class TestWaveformAutoCut(unittest.TestCase):
    def setUp(self):
        self.sample_rate = 16000
        self.pcm = generate_synthetic_pcm(
            sample_rate=self.sample_rate,
            tone_duration=1.0,
            silence_duration=3.0,
            frequency=440.0
        )

    def test_compute_rms_profile(self):
        profile = compute_rms_profile(self.pcm, sample_rate=self.sample_rate, frame_duration_ms=20.0)
        self.assertTrue(len(profile) > 0)
        # Tone at 0.5s should have high energy (approx -6 to -12 dB)
        speech_frame = [p for p in profile if 0.4 <= p[0] <= 0.6][0]
        self.assertGreater(speech_frame[1], -20.0)
        
        # Silence at 2.5s should have low energy (< -80 dB)
        silence_frame = [p for p in profile if 2.4 <= p[0] <= 2.6][0]
        self.assertLess(silence_frame[1], -60.0)

    def test_zero_crossing_snapping(self):
        # Target time 1.0s
        snap_time = find_zero_crossing(self.pcm, 1.0, search_window_ms=25.0, sample_rate=self.sample_rate)
        # Verify sample at snap_time is near zero
        sample_idx = int(snap_time * self.sample_rate)
        chunk = self.pcm[sample_idx * 2 : (sample_idx + 2) * 2]
        s1, s2 = struct.unpack("<2h", chunk)
        # Zero-crossing condition: product <= 0 or magnitude is very small
        self.assertTrue(s1 * s2 <= 0 or abs(s1) < 500)

    def test_snap_boundary_preserves_speech_release_padding(self):
        # Speech ended nominally at 0.95s, but acoustic decay persists
        snapped = snap_boundary_to_waveform_silence(
            self.pcm,
            raw_time=0.95,
            mode="speech_release",
            silence_threshold_db=-32.0,
            min_speech_padding_ms=150.0,
            sample_rate=self.sample_rate
        )
        # Snapped time must be AT LEAST raw_time + 150ms padding (>= 1.10s)
        self.assertGreaterEqual(snapped, 1.09)

    def test_detect_mechanical_stutters(self):
        words = [
            {"text": "I", "start": 0.0, "end": 0.2},
            {"text": "mean", "start": 0.25, "end": 0.5},
            {"text": "I", "start": 0.8, "end": 1.0},
            {"text": "mean", "start": 1.05, "end": 1.3},
            {"text": "Dispatch", "start": 1.4, "end": 1.8},
        ]
        proposals = detect_mechanical_stutters(words)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["type"], "false_start")
        self.assertEqual(proposals[0]["source_start"], 0.0)
        self.assertEqual(proposals[0]["source_end"], 0.8)
        self.assertEqual(proposals[0]["status"], "candidate")

    def test_preserve_self_corrections(self):
        words = [
            {"text": "I", "start": 0.0, "end": 0.2},
            {"text": "think", "start": 0.25, "end": 0.5},
            {"text": "actually", "start": 0.7, "end": 1.0},
            {"text": "no", "start": 1.05, "end": 1.2},
            {"text": "it's", "start": 1.3, "end": 1.5},
        ]
        proposals = detect_mechanical_stutters(words)
        self.assertEqual(len(proposals), 0, "Self-corrections must never be flagged as stutters")


    def test_detect_mechanical_stutters_accepts_canonical_gemini_word_schema(self):
        canonical = [
            {"word": "I", "start": 0.0, "end": 0.2},
            {"word": "mean", "start": 0.25, "end": 0.5},
            {"word": "I", "start": 0.8, "end": 1.0},
            {"word": "mean", "start": 1.05, "end": 1.3},
            {"word": "Dispatch", "start": 1.4, "end": 1.8},
        ]
        legacy = [
            {"text": item["word"], "start": item["start"], "end": item["end"]}
            for item in canonical
        ]
        self.assertEqual(
            detect_mechanical_stutters(canonical),
            detect_mechanical_stutters(legacy),
        )
        self.assertEqual(len(detect_mechanical_stutters(canonical)), 1)

    def test_contextual_like_classifier_preserves_lexical_uses_and_reviews_ambiguous_uses(self):
        words = [
            {"word": "I", "start": 0.0, "end": 0.1},
            {"word": "like", "start": 0.1, "end": 0.3},
            {"word": "this", "start": 0.3, "end": 0.5},
            {"word": "it", "start": 1.0, "end": 1.1},
            {"word": "was", "start": 1.1, "end": 1.2},
            {"word": "like,", "start": 1.2, "end": 1.4},
            {"word": "really", "start": 1.4, "end": 1.7},
            {"word": "expensive", "start": 1.7, "end": 2.1},
            {"word": "looks", "start": 2.2, "end": 2.4},
            {"word": "like", "start": 2.4, "end": 2.6},
            {"word": "a", "start": 2.6, "end": 2.7},
            {"word": "deal", "start": 2.7, "end": 3.0},
        ]
        proposals = detect_contextual_filler_words(words)
        like_decisions = [p["decision"] for p in proposals if p["word"].lower().startswith("like")]
        self.assertEqual(like_decisions, ["PRESERVE", "REVIEW", "PRESERVE"])

    def test_clear_hesitation_vocalization_is_non_destructive_auto_remove_proposal(self):
        proposals = detect_contextual_filler_words([
            {"word": "um,", "start": 0.4, "end": 0.7},
            {"word": "मतलब", "start": 0.8, "end": 1.1},
        ])
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["decision"], "AUTO-REMOVE")
        self.assertEqual(proposals[0]["status"], "candidate")
        self.assertEqual(proposals[0]["word"], "um,")

    def test_rms_vad_verifies_silence_but_not_speech(self):
        self.assertTrue(is_waveform_interval_silent(self.pcm, 1.2, 3.8, sample_rate=self.sample_rate))
        self.assertFalse(is_waveform_interval_silent(self.pcm, 0.2, 0.8, sample_rate=self.sample_rate))

    def test_autocut_combines_waveform_and_transcript(self):
        # Transcript with a 3.0s dead air gap (1.0s to 4.0s)
        segments = [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "First thought here.",
                "words": [
                    {"text": "First", "start": 0.0, "end": 0.3},
                    {"text": "thought", "start": 0.35, "end": 0.7},
                    {"text": "here.", "start": 0.75, "end": 1.0},
                ]
            },
            {
                "start": 4.0,
                "end": 5.0,
                "text": "Second thought starts.",
                "words": [
                    {"text": "Second", "start": 4.0, "end": 4.4},
                    {"text": "thought", "start": 4.45, "end": 4.7},
                    {"text": "starts.", "start": 4.75, "end": 5.0},
                ]
            }
        ]
        proposals = analyze_waveform_and_transcript_autocuts(
            media_path=None,
            transcript_segments=segments,
            clip_start=0.0,
            clip_end=5.0,
            raw_pcm_bytes=self.pcm,
            min_dead_air=2.5,
            target_pause=0.8
        )
        self.assertEqual(len(proposals), 1)
        prop = proposals[0]
        self.assertEqual(prop["type"], "dead_air")
        self.assertEqual(prop["status"], "candidate")
        # Ensure cut starts AFTER the word ending with acoustic release padding >= 150ms
        self.assertGreaterEqual(prop["source_start"], 1.15)
        # Ensure cut ends BEFORE the next word with attack padding
        self.assertLessEqual(prop["source_end"], 4.0)



    def test_detect_audio_clipping(self):
        """Audio clipping events are flagged as REVIEW defects with sample count."""
        samples = [500] * 16000
        # Inject 10 clipped samples at 0.5s
        for i in range(8000, 8010):
            samples[i] = 32767
        pcm = struct.pack("<16000h", *samples)
        events = detect_audio_clipping(pcm, sample_rate=16000, threshold_db=-0.5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "clipping")
        self.assertEqual(events[0]["decision"], "REVIEW")
        self.assertAlmostEqual(events[0]["source_start"], 0.5, delta=0.01)
        self.assertEqual(events[0]["samples_clipped"], 10)

    def test_detect_transient_clicks(self):
        """High-crest spikes in low-noise background are flagged as transient clicks."""
        samples = [100] * 16000
        # Inject isolated 5-sample spike at 0.25s
        for i in range(4000, 4005):
            samples[i] = 22000
        pcm = struct.pack("<16000h", *samples)
        clicks = detect_transient_clicks(pcm, sample_rate=16000)
        self.assertEqual(len(clicks), 1)
        self.assertEqual(clicks[0]["type"], "transient_click")
        self.assertEqual(clicks[0]["decision"], "REVIEW")
        self.assertAlmostEqual(clicks[0]["source_start"], 0.25, delta=0.02)

    def test_detect_breaths_in_pause(self):
        """Turbulent airflow in non-speech pause is classified as a breath candidate."""
        import random
        random.seed(123)
        samples = [0] * 32000
        # Breath noise between 0.6s and 0.9s (samples 9600 to 14400)
        for i in range(9600, 14400):
            samples[i] = int(random.uniform(-1400, 1400))
        pcm = struct.pack("<32000h", *samples)
        speech = [(0.0, 0.5), (1.1, 2.0)]
        events = detect_breaths_and_coughs(pcm, speech, sample_rate=16000)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "breath")
        self.assertEqual(events[0]["decision"], "REVIEW")
        self.assertAlmostEqual(events[0]["source_start"], 0.6, delta=0.06)

    def test_autocut_complete_triage_mix(self):
        """Mixed input produces all 3 triage decisions: AUTO-REMOVE, PRESERVE, REVIEW."""
        transcript = [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "Um I really like coding but like I don't know.",
                "words": [
                    {"word": "Um", "start": 0.0, "end": 0.3},
                    {"word": "I", "start": 0.4, "end": 0.6},
                    {"word": "really", "start": 0.65, "end": 0.9},
                    {"word": "like", "start": 0.95, "end": 1.2},      # Lexical verb -> PRESERVE
                    {"word": "coding", "start": 1.25, "end": 1.6},
                    {"word": "but", "start": 1.65, "end": 1.8},
                    {"word": "like", "start": 1.85, "end": 2.1},     # Hesitation -> REVIEW
                    {"word": "I", "start": 2.2, "end": 2.4},
                    {"word": "don't", "start": 2.45, "end": 2.7},
                    {"word": "know.", "start": 2.75, "end": 3.0},
                ]
            }
        ]
        proposals = analyze_waveform_and_transcript_autocuts(
            media_path=None,
            transcript_segments=transcript,
            clip_start=0.0,
            clip_end=3.0,
            raw_pcm_bytes=None,
        )
        decisions = {p["decision"] for p in proposals}
        self.assertIn("AUTO-REMOVE", decisions)
        self.assertIn("PRESERVE", decisions)
        self.assertIn("REVIEW", decisions)

if __name__ == "__main__":
    unittest.main()
