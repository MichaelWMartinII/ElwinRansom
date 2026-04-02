"""Parse agent.conf and provide companion defaults."""

import os
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CONF = _ROOT / "agent.conf"


def _parse_conf(path: Path) -> dict[str, str]:
    """Parse a simple KEY=\"VALUE\" shell config file."""
    vals: dict[str, str] = {}
    if not path.exists():
        return vals
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^([A-Z_]+)\s*=\s*"?(.*?)"?\s*$', line)
        if m:
            vals[m.group(1)] = m.group(2)
    return vals


_conf = _parse_conf(_CONF)

# Model selection
MODEL = _conf.get("MODEL", "")

# LLM server
LLM_HOST = _conf.get("HOST", "127.0.0.1")
LLM_PORT = int(_conf.get("PORT", "8080"))
LLM_API_KEY = _conf.get("API_KEY", "")
LLM_BASE_URL = f"http://{LLM_HOST}:{LLM_PORT}"

# Context budget — all values scale with CTX_SIZE so changing agent.conf
# is the only knob needed.
CTX_SIZE = int(_conf.get("CTX_SIZE", "4096"))
RESPONSE_BUDGET  = max(256, CTX_SIZE // 4)        # 25% for the reply
PROMPT_BUDGET    = CTX_SIZE - RESPONSE_BUDGET      # 75% for the input

SYSTEM_TOKEN_BUDGET = max(400, CTX_SIZE // 6)      # ~17% for system prompt
MEMORY_TOKEN_BUDGET = max(200, CTX_SIZE // 8)      # ~12% for retrieved memories
RECENT_TOKEN_BUDGET = PROMPT_BUDGET - SYSTEM_TOKEN_BUDGET - MEMORY_TOKEN_BUDGET

# Paths
DATA_DIR = _ROOT / "memories"
DB_PATH = DATA_DIR / "companion.db"
DORY_DB_PATH = Path(_conf.get("DORY_DB_PATH", str(DATA_DIR / "dory_elwin.db"))).expanduser()

# Embedding
EMBED_MODEL = "all-MiniLM-L6-v2"
EMBED_DIM = 384
TOP_K_MEMORIES = 5

# Telegram
TELEGRAM_BOT_TOKEN = _conf.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_OWNER_ID = int(_conf.get("TELEGRAM_OWNER_ID", "0") or "0")

# Brave Search
BRAVE_SEARCH_API_KEY = _conf.get("BRAVE_SEARCH_API_KEY", "")

# Vision server
VISION_HOST = _conf.get("VISION_HOST", "127.0.0.1")
VISION_PORT = int(_conf.get("VISION_PORT", "55507"))
VISION_API_KEY = _conf.get("VISION_API_KEY", "")
VISION_BASE_URL = f"http://{VISION_HOST}:{VISION_PORT}"

# Butler / briefing
BRIEFING_HOUR = int(_conf.get("BRIEFING_HOUR", "8"))
BRIEFING_MINUTE = int(_conf.get("BRIEFING_MINUTE", "0"))
LOCATION = _conf.get("LOCATION", "Murfreesboro, TN")

# Web UI
WEB_HOST          = _conf.get("WEB_HOST", "0.0.0.0")
WEB_PORT          = int(_conf.get("WEB_PORT", "7272"))
WEB_PASSWORD      = _conf.get("WEB_PASSWORD", "")
TLS_CERT_PATH     = _conf.get("TLS_CERT_PATH", "")
TLS_KEY_PATH      = _conf.get("TLS_KEY_PATH", "")

# Web Push (VAPID)
VAPID_PRIVATE_KEY = _conf.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY  = _conf.get("VAPID_PUBLIC_KEY", "")
VAPID_CLAIM_EMAIL = _conf.get("VAPID_CLAIM_EMAIL", "")

# Dory integration
DORY_ENABLED = _conf.get("DORY_ENABLED", "1").lower() not in {"0", "false", "no", "off"}
DORY_MODE = _conf.get("DORY_MODE", "stable").strip().lower() or "stable"

_DORY_MODE_DEFAULTS = {
    "stable": {"observer_threshold": 8, "flush_turn_threshold": 12},
    "aggressive": {"observer_threshold": 3, "flush_turn_threshold": 4},
    "manual": {"observer_threshold": 999999, "flush_turn_threshold": 999999},
}
_dory_defaults = _DORY_MODE_DEFAULTS.get(DORY_MODE, _DORY_MODE_DEFAULTS["stable"])

DORY_OBSERVER_THRESHOLD = int(
    _conf.get("DORY_OBSERVER_THRESHOLD", str(_dory_defaults["observer_threshold"]))
)
DORY_FLUSH_TURN_THRESHOLD = int(
    _conf.get("DORY_FLUSH_TURN_THRESHOLD", str(_dory_defaults["flush_turn_threshold"]))
)
