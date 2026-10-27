"""Минимальный клиент Telegram Bot API на стандартной библиотеке: вызов метода и отправка файла."""
import json
import urllib.parse
import urllib.request
import uuid


def vyzov(token: str, method: str, taymaut: int = 30, **params) -> dict:
    data = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
            for k, v in params.items() if v is not None}
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}",
                                 data=urllib.parse.urlencode(data).encode())
    try:
        with urllib.request.urlopen(req, timeout=taymaut) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


def poslat_dokument(token: str, chat_id: int, filename: str, content: bytes, taymaut: int = 120) -> dict:
    """multipart собирается вручную: имя файла с запятыми и кириллицей уходит как есть."""
    boundary = uuid.uuid4().hex
    safe_name = filename.replace('"', "'")
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{safe_name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendDocument", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=taymaut) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")
