"""Input controller — normalizes any input type into text.

Routes by InputType:
  TEXT  → pass through unchanged
  IMAGE → vision model describes the image, returns "[Image: ...]\ncaption"

Sits upstream of the LLM pipeline so the rest of the system only sees text.
"""

import enum

from . import audio, vision


class InputType(enum.Enum):
    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"


def process_input(
    input_type: InputType,
    text: str = "",
    image_path: str | None = None,
    audio_path: str | None = None,
) -> str:
    """Convert any supported input into a text string for the LLM pipeline.

    Args:
        input_type: What kind of input this is.
        text: User-provided text (caption for images, message for text).
        image_path: Path to image file (required when input_type is IMAGE).
        audio_path: Path to audio file (required when input_type is VOICE).

    Returns:
        Normalized text ready for the LLM pipeline.
    """
    if input_type == InputType.TEXT:
        return text

    if input_type == InputType.IMAGE:
        # If caption ends with '?', use it as a VQA question
        question = text.strip() if text.strip().endswith("?") else None
        description = vision.describe_image(image_path, question=question)

        if text.strip():
            return f"[Image: {description}]\n{text}"
        return f"[Image: {description}]"

    if input_type == InputType.VOICE:
        return audio.transcribe(audio_path)

    raise ValueError(f"Unsupported input type: {input_type}")
