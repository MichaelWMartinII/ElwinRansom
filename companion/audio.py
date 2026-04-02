"""Speech-to-text via faster-whisper (CTranslate2).

Converts any audio file to 16 kHz mono WAV via ffmpeg, then transcribes
with the faster-whisper `base.en` model. No external server needed.
"""

import logging
import subprocess

import numpy as np

logger = logging.getLogger(__name__)

# Lazy singleton — loaded once on first transcription, stays in memory (~150 MB).
_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        logger.info("Loading faster-whisper model (base.en) ...")
        _model = WhisperModel("base.en", device="cpu", compute_type="int8")
        logger.info("faster-whisper model loaded.")
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

    # Convert raw PCM to float32 numpy array (what faster-whisper expects)
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

    # Transcribe
    model = _get_model()
    segments, _info = model.transcribe(audio, language="en")
    text = " ".join(seg.text.strip() for seg in segments).strip()

    logger.info("Transcription: %s", text[:120])
    return text
