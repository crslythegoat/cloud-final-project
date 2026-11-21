"""Create missing local API/bot secrets without printing them or replacing DB URIs."""

import json
import secrets

from dotenv import dotenv_values, set_key

from app.core.config import PROJECT_ROOT


def main():
    path = PROJECT_ROOT / ".env"
    values = dotenv_values(path, encoding="utf-8-sig")
    if not values.get("CLOUD_DB_API_TOKEN"):
        set_key(path, "CLOUD_DB_API_TOKEN", secrets.token_urlsafe(36), encoding="utf-8-sig")
    tokens = json.loads(values.get("CLOUD_DB_BOT_TOKENS") or "{}")
    for number in range(1, 10):
        tokens.setdefault(f"bot-{number}", secrets.token_urlsafe(36))
    set_key(path, "CLOUD_DB_BOT_TOKENS", json.dumps(tokens, separators=(",", ":")), encoding="utf-8-sig")
    print("Local tokens configured in .env. Existing nonempty tokens preserved.")


if __name__ == "__main__":
    main()
