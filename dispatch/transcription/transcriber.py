"""Speech-to-text transcription engine using faster-whisper.
Extracts word-level timestamps, handles Hinglish code-switching, and formats structured transcript data.
"""
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from dispatch.transcription.audio import extract_audio
from dispatch import db

logger = logging.getLogger("dispatch.transcription")

# Lazy-loaded singleton for WhisperModel
_WHISPER_MODEL = None


def get_whisper_model(model_size: str = "base", device: str = "cpu", compute_type: str = "int8"):
    """Load or return cached WhisperModel instance optimized for AMD CPU multithreading."""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        try:
            from faster_whisper import WhisperModel
            logger.info("Initializing faster-whisper model: size=%s, device=%s, compute=%s",
                        model_size, device, compute_type)
            _WHISPER_MODEL = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type,
                cpu_threads=4
            )
        except Exception as e:
            logger.error("Failed to load faster-whisper model: %s", e)
            raise
    return _WHISPER_MODEL


def transcribe_video(
    video_path: Path,
    chunk_id: Optional[str] = None,
    session_id: Optional[str] = None,
    model_size: str = "base",
    initial_prompt: Optional[str] = None
) -> Dict[str, Any]:
    """Transcribe video audio to text with word-level timestamps and VAD silence filtering.
    
    Args:
        video_path: Path to the video file.
        chunk_id: Database chunk ID (if registering to DB).
        session_id: Database session ID.
        model_size: Model size ("base", "small", "medium").
        initial_prompt: Prompt hint to encourage Roman Hinglish transcription.
        
    Returns:
        Dict containing full_text, segments (with word timestamps), and audio_path.
    """
    logger.info("Extracting audio from %s", video_path.name)
    audio_path = extract_audio(video_path)

    # Prompt hint for Hinglish if not supplied
    if not initial_prompt:
        initial_prompt = "Main aaj discuss karunga video creation, coding, automation and workflow design."

    model = get_whisper_model(model_size=model_size)

    logger.info("Starting speech transcription with word-level timestamps and VAD on %s", audio_path.name)
    segments_raw, info = model.transcribe(
        str(audio_path),
        beam_size=1,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        initial_prompt=initial_prompt
    )

    full_text_pieces = []
    formatted_segments = []

    for seg in segments_raw:
        seg_text = seg.text.strip()
        if not seg_text:
            continue

        full_text_pieces.append(seg_text)

        words_data = []
        if seg.words:
            for w in seg.words:
                words_data.append({
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "probability": round(w.probability, 3)
                })

        formatted_segments.append({
            "id": seg.id,
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg_text,
            "words": words_data
        })

    # Resilient fallback: If VAD filtered everything, try once without VAD filter
    if not formatted_segments and audio_path.exists():
        logger.info("VAD detected 0 segments. Retrying transcription with vad_filter=False fallback.")
        segments_raw, info = model.transcribe(
            str(audio_path),
            beam_size=3,
            word_timestamps=True,
            vad_filter=False,
            initial_prompt=initial_prompt
        )
        for seg in segments_raw:
            seg_text = seg.text.strip()
            if not seg_text:
                continue
            full_text_pieces.append(seg_text)
            words_data = []
            if seg.words:
                for w in seg.words:
                    words_data.append({
                        "word": w.word.strip(),
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                        "probability": round(w.probability, 3)
                    })
            formatted_segments.append({
                "id": seg.id,
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": seg_text,
                "words": words_data
            })

    full_text = " ".join(full_text_pieces)
    logger.info("Transcription completed: %d segments, %d total words",
                len(formatted_segments), len(full_text.split()))

    # Clean up temporary .wav file to conserve disk space
    try:
        audio_path.unlink()
    except Exception as e:
        logger.debug("Could not remove temp audio file: %s", e)

    # Save to SQLite database if chunk_id is provided
    transcript_id = None
    if chunk_id:
        transcript_id = db.save_transcript(
            chunk_id=chunk_id,
            session_id=session_id,
            full_text=full_text,
            segments=formatted_segments
        )

    return {
        "transcript_id": transcript_id,
        "full_text": full_text,
        "segments": formatted_segments,
        "language": info.language,
        "language_probability": info.language_probability
    }
