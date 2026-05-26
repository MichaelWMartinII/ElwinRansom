"""Text-to-speech synthesis using Kokoro ONNX.

Uses kokoro-v1.0 (int8) with af_heart (American Female, warm).
Outputs OGG Opus for Telegram reply_voice.

Model files live in models/ at the project root (downloaded once).
Lazy-loads the Kokoro instance on first call so bot startup stays fast.

Callers own the returned OGG path and must unlink it after use.
"""

import logging
import subprocess
import tempfile
from pathlib import Path

import soundfile as sf

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_MODEL_PATH = _ROOT / "models" / "kokoro-v1.0.int8.onnx"
_VOICES_PATH = _ROOT / "models" / "voices-v1.0.bin"

_VOICE = "am_liam"    # American Male
_SAMPLE_RATE = 24000

_kokoro = None


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(_MODEL_PATH), str(_VOICES_PATH))
    return _kokoro


def synthesize(text: str) -> str:
    """Synthesize text to speech.

    Returns the path to a temporary OGG Opus file. Caller must unlink it.
    Raises RuntimeError on failure.
    """
    kokoro = _get_kokoro()
    samples, sample_rate = kokoro.create(
        text=text,
        voice=_VOICE,
        speed=1.0,
        lang="en-us",
    )

    wav_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav_tmp.close()
    sf.write(wav_tmp.name, samples, sample_rate)

    ogg_tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
    ogg_tmp.close()

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", wav_tmp.name,
                "-c:a", "libopus",
                "-b:a", "64k",
                "-loglevel", "error",
                ogg_tmp.name,
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            Path(ogg_tmp.name).unlink(missing_ok=True)
            raise RuntimeError(
                result.stderr.decode("utf-8", errors="replace").strip()
            )
    finally:
        Path(wav_tmp.name).unlink(missing_ok=True)

    return ogg_tmp.name
