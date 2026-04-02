"""Image understanding via a local vision llama-server (Qwen3-VL-2B).

Sends images to a second llama-server running on VISION_PORT with
multimodal support. Uses the same OpenAI-compatible chat completions
format as the main LLM server — all local, no external calls.
"""

import base64
import json
import logging
import urllib.request
import urllib.error

from . import config

logger = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if config.VISION_API_KEY:
        h["Authorization"] = f"Bearer {config.VISION_API_KEY}"
    return h


def health_check() -> bool:
    """Return True if the vision server is reachable."""
    try:
        req = urllib.request.Request(
            f"{config.VISION_BASE_URL}/health", headers=_headers()
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def describe_image(image_path: str, question: str | None = None) -> str:
    """Send an image to the vision server and get a text description.

    Args:
        image_path: Path to an image file (JPEG, PNG, etc.).
        question: Optional question about the image (VQA mode).
                  If None, asks for a general identification/description.

    Returns:
        Text description or answer from the vision model.
    """
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("ascii")

    prompt = question if question else "Identify and describe what you see in this image. Be specific and factual."

    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_data}",
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ],
        "temperature": 0.7,
        "top_p": 0.8,
        "max_tokens": 1024,
    }

    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{config.VISION_BASE_URL}/v1/chat/completions",
        data=data,
        headers=_headers(),
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            obj = json.loads(resp.read())
            result = obj["choices"][0]["message"]["content"]
            logger.info("Vision result: %s", result[:120])
            return result.strip()
    except urllib.error.URLError as e:
        raise ConnectionError(
            f"Vision server not reachable at {config.VISION_BASE_URL} — "
            "start it with: ./start-vision.sh"
        ) from e
