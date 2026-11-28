"""Writes .env.production from .env.production.example with fresh random secrets.

    python3 deploy/make_env.py your-name.centralindia.cloudapp.azure.com

It fills DOMAIN and every GENERATE value. The provider keys, the YouTube client and the mail
settings are left empty for you to fill in. It refuses to overwrite an existing file, because
a new TOKEN_ENCRYPTION_KEY or POSTGRES_PASSWORD would lock you out of the data already there.
Needs only Python 3's standard library.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / ".env.production"


def main() -> int:
    if len(sys.argv) != 2 or "/" in sys.argv[1] or " " in sys.argv[1]:
        print("Usage: python3 deploy/make_env.py <domain>   (for example app.example.com)")
        return 2
    if TARGET.exists():
        print(f"{TARGET} already exists. Edit it by hand; it is not overwritten.")
        return 1
    made = {
        "DOMAIN": sys.argv[1].strip().lower(),
        "SECRET_KEY": secrets.token_urlsafe(48),
        # Hex only: this one goes inside the database address.
        "POSTGRES_PASSWORD": secrets.token_hex(24),
        "S3_SECRET_KEY": secrets.token_hex(24),
        "RENDER_SERVICE_TOKEN": secrets.token_urlsafe(40),
        # A Fernet key: 32 random bytes, url-safe base64.
        "TOKEN_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    }
    lines = []
    for line in (ROOT / ".env.production.example").read_text(encoding="utf-8").splitlines():
        name = line.split("=", 1)[0]
        lines.append(f"{name}={made[name]}" if "=" in line and name in made else line)
    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        TARGET.chmod(0o600)  # readable by you only
    except OSError:
        pass
    print(f"Wrote {TARGET} with new secrets. Now fill in the provider keys and the mail settings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
