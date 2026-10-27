#!/usr/bin/env python3
"""Генерирует валидный Telegram initData владельца — для проверки Mini App без телефона
(строку можно подставить в мок Telegram.WebApp). Токен бота не печатается, только подпись."""
import hashlib
import hmac
import json
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def make_init_data(bot_token: str, user_id: int, first_name: str = "Demo") -> str:
    user = json.dumps({"id": user_id, "first_name": first_name, "language_code": "ru",
                       "allows_write_to_pm": True}, ensure_ascii=False, separators=(",", ":"))
    fields = {"auth_date": str(int(time.time())), "query_id": "AAHtest", "user": user}
    line = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, line.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


if __name__ == "__main__":
    import config
    print(make_init_data(config.BOT_TOKEN, config.OWNER_ID))
