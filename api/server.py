# Приём ответов от сервиса опросов (вебхук).
#
# Сервис опросов присылает заполненные анкеты POST-запросом.
# Ключ принимается из: заголовка X-API-Key, заголовка
# Authorization: Bearer, параметра ?key= или полей key / api_key
# в теле запроса. Принятые анкеты дописываются построчно
# в /data/answers.jsonl. Только стандартная библиотека.

import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "3000"))
API_KEY = os.environ.get("API_KEY", "")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
DATA_FILE = os.path.join(DATA_DIR, "answers.jsonl")

# Платформа опросов: адрес API и id формы, которую мы показываем
FORMS_API = os.environ.get("FORMS_API_BASE", "https://forms.hr.alabuga.digital/api")
FORM_UUID = os.environ.get("FORM_UUID", "ffca186d-804c-4245-874c-434581a59d96")

# Токен для страницы настроек /setting/ (задаётся в .env)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")


def extract_key(path, headers, body):
    for name in ("X-Api-Key", "X-API-Key"):
        if headers.get(name):
            return headers[name]

    auth = headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]

    query = parse_qs(urlparse(path).query)
    if "key" in query:
        return query["key"][0]

    try:
        data = json.loads(body or "null")
        if isinstance(data, dict):
            return data.get("key") or data.get("api_key") or data.get("apiKey") or ""
    except Exception:
        pass
    return ""


def safe_equal(a, b):
    return hmac.compare_digest(str(a), str(b))


def extract_admin_token(path, headers, body):
    for name in ("X-Admin-Token", "X-Admin-Key"):
        if headers.get(name):
            return headers[name]
    auth = headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    query = parse_qs(urlparse(path).query)
    if "token" in query:
        return query["token"][0]
    return ""


UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def default_form_url():
    origin = FORMS_API
    if origin.endswith("/api"):
        origin = origin[:-4]
    return f"{origin}/forms/{FORM_UUID}/pass"


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cfg = {}
    return {
        "uuid": cfg.get("uuid") or FORM_UUID,
        "form_url": cfg.get("form_url") or default_form_url(),
    }


def save_config(form_url, uuid):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"form_url": form_url, "uuid": uuid}, f, ensure_ascii=False)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _current_uuid(self):
        return load_config()["uuid"]

    def _answer_stats(self):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                lines = [line for line in f if line.strip()]
        except FileNotFoundError:
            lines = []
        count = len(lines)
        last_ts = json.loads(lines[-1]).get("ts") if lines else None
        return count, last_ts

    def _admin_ok(self, body):
        token = extract_admin_token(self.path, self.headers, body)
        if ADMIN_TOKEN and safe_equal(token, ADMIN_TOKEN):
            return True
        self._send_json(401, {"ok": False, "error": "invalid admin token"})
        return False

    def _resolve_form_uuid(self, url):
        m = UUID_RE.search(url)
        if m:
            return m.group(0)
        # Возможно, короткая ссылка — пробуем пройти по редиректам
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                final = resp.geturl()
            m = UUID_RE.search(final)
            if m:
                return m.group(0)
        except Exception:
            pass
        return None

    def _fetch_form_def_by_uuid(self, uuid):
        url = f"{FORMS_API}/forms/{uuid}/"
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")

    def _platform_form_def(self):
        return self._fetch_form_def_by_uuid(self._current_uuid())

    def _platform_complete(self, answers):
        req = urllib.request.Request(
            f"{FORMS_API}/forms/{self._current_uuid()}/complete",
            data=json.dumps({"answers": answers}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            self._send_json(200, {"ok": True})
            return

        # Ссылка на текущую форму — открыта (нужна для QR на главной)
        if path == "/form-link":
            self._send_json(200, {"ok": True, "form_url": load_config()["form_url"]})
            return

        # Определение формы (вопросы) — для нашей копии страницы опроса
        if path == "/form":
            try:
                status, body = self._platform_form_def()
            except Exception:
                self._send_json(502, {"ok": False, "error": "platform unavailable"})
                return
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = {"ok": False, "raw": body[:500]}
            self._send_json(status, parsed)
            return

        # Метка последнего принятого ответа — открыта без ключа:
        # по ней страница стойки понимает, что участник отправил форму.
        if path == "/last":
            try:
                with open(DATA_FILE, encoding="utf-8") as f:
                    lines = [line for line in f if line.strip()]
            except FileNotFoundError:
                lines = []
            if lines:
                last = json.loads(lines[-1])
                self._send_json(200, {"ok": True, "ts": last.get("ts"), "count": len(lines)})
            else:
                self._send_json(200, {"ok": True, "ts": None, "count": 0})
            return

        # Длинный опрос: если появился ответ новее ts — возвращаем сразу,
        # иначе держим соединение до 25 секунд. Фоновые вкладки браузеров
        # не урезают такие ожидающие запросы, поэтому страница стойки
        # узнаёт о новом ответе мгновенно.
        if path == "/wait":
            query = parse_qs(urlparse(self.path).query)
            client_ts = (query.get("ts") or [""])[0]
            deadline = time.monotonic() + 25.0
            while True:
                try:
                    with open(DATA_FILE, encoding="utf-8") as f:
                        lines = [line for line in f if line.strip()]
                except FileNotFoundError:
                    lines = []
                latest_ts = json.loads(lines[-1]).get("ts") if lines else None
                if latest_ts != client_ts or time.monotonic() >= deadline:
                    self._send_json(200, {"ok": True, "ts": latest_ts, "count": len(lines)})
                    return
                time.sleep(0.5)

        # Настройки: счётчик + текущая форма (нужен токен администратора)
        if path == "/config":
            if not self._admin_ok(""):
                return
            cfg = load_config()
            count, last_ts = self._answer_stats()
            title = None
            try:
                status, body = self._fetch_form_def_by_uuid(cfg["uuid"])
                if 200 <= status < 300:
                    title = json.loads(body).get("title")
            except Exception:
                pass
            self._send_json(200, {
                "ok": True,
                "form_url": cfg["form_url"],
                "uuid": cfg["uuid"],
                "title": title,
                "answers_count": count,
                "last_ts": last_ts,
            })
            return

        key = extract_key(self.path, self.headers, "")
        if not API_KEY or not safe_equal(key, API_KEY):
            self._send_json(401, {"ok": False, "error": "invalid api key"})
            return

        if path == "/answers":
            try:
                with open(DATA_FILE, encoding="utf-8") as f:
                    answers = [json.loads(line) for line in f if line.strip()]
            except FileNotFoundError:
                answers = []
            self._send_json(200, {"ok": True, "answers": answers})
            return

        self._send_json(405, {"ok": False, "error": "method not allowed"})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""

        # Отправка ответов из нашей копии опроса в платформу.
        # Без ключа: это внутренний вызов страницы стойки.
        if path == "/form-submit":
            try:
                answers = json.loads(raw).get("answers")
                if not isinstance(answers, list):
                    raise ValueError("answers must be a list")
            except Exception:
                self._send_json(400, {"ok": False, "error": "answers required"})
                return
            try:
                status, body = self._platform_complete(answers)
            except Exception:
                self._send_json(502, {"ok": False, "error": "platform unavailable"})
                return
            if 200 <= status < 300:
                # Ответ принят платформой — сохраняем и у себя
                record = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "ip": self.headers.get("X-Real-IP") or self.client_address[0],
                    "data": {"source": "replica", "answers": answers},
                }
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(DATA_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                print("REPLICA ACCEPTED answers=%s" % json.dumps(answers, ensure_ascii=False)[:500], flush=True)
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = {"raw": body[:500]}
            self._send_json(status, parsed)
            return

        # Смена формы на другую (нужен токен администратора)
        if path == "/config":
            if not self._admin_ok(raw):
                return
            try:
                new_url = (json.loads(raw).get("form_url") or "").strip()
            except Exception:
                new_url = ""
            if not new_url:
                self._send_json(400, {"ok": False, "error": "form_url required"})
                return
            uuid = self._resolve_form_uuid(new_url)
            if not uuid:
                self._send_json(400, {"ok": False, "error": "не удалось распознать идентификатор формы по ссылке"})
                return
            try:
                status, body = self._fetch_form_def_by_uuid(uuid)
            except Exception:
                self._send_json(502, {"ok": False, "error": "платформа недоступна"})
                return
            if not (200 <= status < 300):
                self._send_json(400, {"ok": False, "error": "форма не найдена (HTTP %d)" % status})
                return
            try:
                form_def = json.loads(body)
                questions = form_def.get("questions") or []
                title = form_def.get("title")
            except Exception:
                questions, title = [], None
            if not questions:
                self._send_json(400, {"ok": False, "error": "в форме нет вопросов"})
                return
            save_config(new_url, uuid)
            self._send_json(200, {
                "ok": True,
                "form_url": new_url,
                "uuid": uuid,
                "title": title,
                "questions_count": len(questions),
            })
            return

        key = extract_key(self.path, self.headers, raw)
        if not API_KEY or not safe_equal(key, API_KEY):
            # Логируем отклонённый запрос целиком: видно, как именно
            # сервис опросов передаёт ключ, и что он присылает.
            print(
                "WEBHOOK REJECTED path=%s headers=%s body=%s"
                % (
                    self.path,
                    json.dumps(dict(self.headers), ensure_ascii=False),
                    raw[:500],
                ),
                flush=True,
            )
            self._send_json(401, {"ok": False, "error": "invalid api key"})
            return

        if path == "/answers":
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "ip": self.headers.get("X-Real-IP") or self.client_address[0],
                "data": parsed if parsed is not None else raw,
            }
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(DATA_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            print("WEBHOOK ACCEPTED body=%s" % raw[:500], flush=True)
            self._send_json(200, {"ok": True})
            return

        self._send_json(405, {"ok": False, "error": "method not allowed"})

    def log_message(self, fmt, *args):
        print(datetime.now().isoformat(), fmt % args, flush=True)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Answers webhook listening on :{PORT}", flush=True)
    server.serve_forever()
