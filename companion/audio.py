"""Speech-to-text via Qwen3-ASR on MLX (mlx-audio).

Converts any audio file to 16 kHz mono PCM via ffmpeg, then transcribes with
the model named by STT_MODEL (default Qwen3-ASR-1.7B, 4-bit). Runs on the
Apple GPU; no external server needed.
"""

import logging
import subprocess
import threading

import numpy as np

from . import config

logger = logging.getLogger(__name__)

# Lazy singleton — loaded once on first transcription, stays in memory (~1.6 GB,
# ~2.4 GB peak while decoding). MLX generation isn't thread-safe, so calls are
# serialized.
_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        from mlx_audio.stt import load

        logger.info("Loading STT model (%s) ...", config.STT_MODEL)
        _model = load(config.STT_MODEL)
        logger.info("STT model loaded.")
    return _model


def transcribe(audio_path: str) -> str:
    """Transcribe an audio file to text.

    Args:
        audio_path: Path to any audio file ffmpeg can read (.ogg, .wav, .mp3, …).

    Returns:
        Transcribed text, stripped of leading/trailing whitespace.
    """
    # Convert to 16 kHz mono signed-16-bit PCM via ffmpeg (stdout pipe, no temp files)
    cmd = [
        "ffmpeg", "-i", audio_path,
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        "-loglevel", "error",
        "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True, check=True)
    pcm_bytes = result.stdout

    # Convert raw PCM to float32 in [-1, 1]
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size == 0:
        return ""

    with _lock:
        model = _get_model()
        output = model.generate(
            audio,
            language="English",
            hotwords=config.STT_HOTWORDS or None,
        )
    text = (output.text or "").strip()

    logger.info("Transcription: %s", text[:120])
    return text
