"""dispatch.video_engine.tracker
Smart subject & speaker tracking for dynamic crop reframing.

Analyzes video frames across keyframe intervals to detect the primary speaker's
horizontal position, computing a stable, damped crop anchor in the normalized
range [0.0, 1.0] where 0.0 is far left, 0.5 is center, and 1.0 is far right.

Fail-Soft Invariant:
If any detection fails, no faces are present, or dependencies are unavailable,
the module safely falls back to 0.5 (standard center anchor) without throwing exceptions.
"""

import logging
import os
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

_CACHED_DETECTOR = None


def _get_detector():
    global _CACHED_DETECTOR
    if _CACHED_DETECTOR is not None:
        return _CACHED_DETECTOR
    try:
        import cv2
        cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        if os.path.exists(cascade_path):
            detector = cv2.CascadeClassifier(cascade_path)
            if not detector.empty():
                _CACHED_DETECTOR = detector
                return _CACHED_DETECTOR
    except Exception as e:
        logger.warning("Could not initialize face detector: %s", e)
    return None


def detect_subject_crop_anchor(
    video_path: Union[str, Path],
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    num_samples: int = 5
) -> float:
    """Detect the horizontal center of the primary subject across video frames.

    Args:
        video_path: Path to the input video file.
        start_time: Optional start time offset in seconds.
        end_time: Optional end time offset in seconds.
        num_samples: Number of evenly spaced frames to evaluate (default 5).

    Returns:
        float: Normalized horizontal crop anchor in [0.15, 0.85], centered at 0.5.
    """
    path_obj = Path(video_path)
    if not path_obj.exists():
        logger.warning("Video file not found for subject tracking: %s", video_path)
        return 0.5

    try:
        import cv2
        import numpy as np
    except ImportError as e:
        logger.warning("OpenCV or NumPy not available for subject tracking: %s", e)
        return 0.5

    detector = _get_detector()
    if detector is None:
        logger.warning("Haar cascade face detector not available; defaulting to 0.5")
        return 0.5

    try:
        cap = cv2.VideoCapture(str(path_obj))
        if not cap.isOpened():
            logger.warning("OpenCV could not open video: %s", path_obj)
            return 0.5

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        if fps <= 0.0:
            fps = 30.0
        duration = float(total_frames) / fps if total_frames > 0 else 0.0

        if duration <= 0.0:
            cap.release()
            return 0.5

        s_time = max(0.0, float(start_time or 0.0))
        e_time = min(duration, float(end_time or duration))
        if e_time <= s_time:
            e_time = duration
            s_time = 0.0

        sample_times = [
            s_time + (e_time - s_time) * (i + 1) / float(num_samples + 1)
            for i in range(num_samples)
        ]

        centers = []
        for t in sample_times:
            frame_idx = int(t * fps)
            frame_idx = max(0, min(total_frames - 1, frame_idx))
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            h, w = frame.shape[:2]
            if w <= 0 or h <= 0:
                continue

            # Downsample to 480px width for 10x faster Haar evaluation
            target_w = 480
            if w > target_w:
                target_h = int(h * (target_w / float(w)))
                small = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
            else:
                small = frame
                target_w, target_h = w, h

            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            min_dim = int(min(target_h, target_w) * 0.06)
            faces = detector.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=4,
                minSize=(min_dim, min_dim)
            )

            if len(faces) > 0:
                largest = max(faces, key=lambda f: f[2] * f[3])
                cx = (float(largest[0]) + float(largest[2]) / 2.0) / float(target_w)
                centers.append(cx)

        cap.release()

        if not centers:
            logger.info("No faces detected in %s; defaulting to center 0.5", path_obj.name)
            return 0.5

        # Use median to reject transient head turns, occlusions, and outlier movements
        median_anchor = float(np.median(centers))
        # Clamp to safe frame boundary [0.15, 0.85]
        clamped = max(0.15, min(0.85, median_anchor))
        logger.info(
            "Auto-reframe detected subject anchor %.3f (samples: %d) for %s",
            clamped, len(centers), path_obj.name
        )
        return round(clamped, 3)

    except Exception as e:
        logger.error("Exception in detect_subject_crop_anchor for %s: %s", video_path, e, exc_info=True)
        return 0.5
