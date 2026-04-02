"""Laptop camera capture via ffmpeg (avfoundation on macOS).

Captures a single frame from the default camera and writes it to a
temporary JPEG file. Requires camera permission — macOS will prompt
on first use.
"""

import logging
import re
import subprocess
import tempfile

logger = logging.getLogger(__name__)

_MARKER_RE = re.compile(r"\[CAMERA\]", re.IGNORECASE)


def capture() -> str:
    """Capture a single frame from the default camera (device 0).

    Returns the path to a temporary JPEG file.
    Raises RuntimeError if capture fails.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    output_path = tmp.name

    cmd = [
        "ffmpeg", "-y",
        "-f", "avfoundation",
        "-framerate", "30",
        "-i", "0",
        "-vframes", "1",
        "-update", "1",
        "-loglevel", "error",
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
        return output_path
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("Camera capture timed out") from e


def extract_marker(text: str) -> bool:
    """Return True if a [CAMERA] marker is present in text."""
    return bool(_MARKER_RE.search(text))


def strip_marker(text: str) -> str:
    """Remove [CAMERA] marker(s) from text."""
    return _MARKER_RE.sub("", text).strip()
