"""VAPID Web Push delivery.

Standalone module — importable by fire scripts and briefing without
depending on webapp.py.

CLI usage:
    python -m companion.web_push --generate-keys
"""

import json
import logging
import sys

logger = logging.getLogger(__name__)


def generate_vapid_keys() -> tuple[str, str]:
    """Generate a VAPID key pair for Web Push.

    Returns (private_key_b64url, public_key_b64url).

    The private key is the raw 32-byte P-256 scalar encoded as base64url
    (accepted by py_vapid's Vapid.from_string). The public key is the
    uncompressed point (65 bytes) encoded as base64url (used by browsers
    when subscribing).
    """
    import base64
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private_key = ec.generate_private_key(ec.SECP256R1())

    # Raw 32-byte private scalar
    private_int = private_key.private_numbers().private_value
    private_bytes = private_int.to_bytes(32, "big")
    private_b64 = base64.urlsafe_b64encode(private_bytes).rstrip(b"=").decode()

    # Uncompressed public point (04 || x || y, 65 bytes)
    public_bytes = private_key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    public_b64 = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode()

    return private_b64, public_b64


def send_push(conn, title: str, body: str) -> None:
    """Send a Web Push notification to all subscribed endpoints.

    Silently skips if VAPID keys are not configured. Removes subscriptions
    that return 410 Gone (browser unsubscribed). All other failures are
    swallowed — this must never block the caller.
    """
    from . import config
    from . import db as _db

    if not config.VAPID_PRIVATE_KEY or not config.VAPID_PUBLIC_KEY:
        return

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush not installed; skipping Web Push delivery")
        return

    subscriptions = _db.get_push_subscriptions(conn)
    if not subscriptions:
        return

    payload = json.dumps({"title": title, "body": body})
    claims = {"sub": f"mailto:{config.VAPID_CLAIM_EMAIL}"}

    for sub in subscriptions:
        try:
            subscription_info = json.loads(sub["subscription_json"])
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=config.VAPID_PRIVATE_KEY,
                vapid_claims=claims,
            )
        except Exception as exc:
            status = None
            if isinstance(exc, WebPushException) and exc.response is not None:
                status = getattr(exc.response, "status_code", None)
            if status == 410:
                try:
                    _db.delete_push_subscription(conn, sub["endpoint"])
                    logger.info("Removed expired push subscription: %s", sub["endpoint"][:40])
                except Exception:
                    pass
            else:
                logger.debug("Push failed for %s: %s", sub["endpoint"][:40], exc)


if __name__ == "__main__":
    if "--generate-keys" in sys.argv:
        priv, pub = generate_vapid_keys()
        print(f'VAPID_PRIVATE_KEY="{priv}"')
        print(f'VAPID_PUBLIC_KEY="{pub}"')
    else:
        print("Usage: python -m companion.web_push --generate-keys")
        sys.exit(1)
