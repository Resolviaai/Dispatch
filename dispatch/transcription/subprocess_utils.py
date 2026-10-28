"""Bounded subprocess execution helpers for the transcription pipeline.

Every FFmpeg call on the single transcription thread must carry a hard wall-clock
deadline: one wedged compressor or splitter previously blocked ALL transcription
indefinitely because the calls had no timeout (audit 2026-10-27). On deadline the
child is killed and the error surfaces as a RuntimeError carrying the tool's
stderr tail, instead of a bare TimeoutExpired ghost the callers must decode.
"""
import subprocess
from typing import List, Sequence


def run_checked(
    cmd: Sequence[str],
    timeout_seconds: float,
    *,
    label: str = "ffmpeg",
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run an ffmpeg-family command with a hard deadline, returning its result.

    stdout is captured by default because several callers (duration probes,
    segmentation output parsing) READ the tool's stdout; a caller that only
    needs side effects may pass capture=False. stderr is always captured.
    Raises RuntimeError on timeout (after the child is killed by subprocess.run).
    Callers check `returncode`/side effects themselves, exactly like they did
    with subprocess.run — this helper only adds the deadline.
    """
    cmd_list: List[str] = list(cmd)
    try:
        return subprocess.run(
            cmd_list,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        detail = ""
        if exc.stderr:
            detail = exc.stderr.strip() if isinstance(exc.stderr, str) else exc.stderr.decode("utf-8", "replace").strip()
        suffix = f"; stderr: {detail}" if detail else ""
        raise RuntimeError(f"{label} did not finish within {timeout_seconds:.0f}s (timed out){suffix}") from exc
