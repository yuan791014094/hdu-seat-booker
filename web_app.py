# -*- coding: utf-8 -*-
import datetime
import ctypes
import io
import json
import locale
import os
import subprocess
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from book import (
    CST,
    DEFAULT_STUDY_ROOM_NAME,
    DEFAULT_BOOKING_FILE,
    DEFAULT_CONFIG_FILE,
    DEFAULT_COOKIE_FILE,
    LibraryBooker,
    STUDY_ROOM_NAMES,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TASK_NAME = "HDU Library Booker"
TIMED_LOG_FILE = os.path.join(SCRIPT_DIR, "timed_booking.log")
INDEX_FILE = os.path.join(SCRIPT_DIR, "web_index.html")
TIMED_PROCESS = None


def abs_path(path):
    return path if os.path.isabs(path) else os.path.join(SCRIPT_DIR, path)


def make_booker():
    booker = LibraryBooker(
        cfg_path=DEFAULT_CONFIG_FILE,
        booking_path=DEFAULT_BOOKING_FILE,
        headless=True,
        cookie_file=DEFAULT_COOKIE_FILE,
    )
    if not booker.login():
        raise RuntimeError("登录失败：请先运行 python book.py --show-browser --dry-run 手动登录一次")
    return booker


def target_summary(booker):
    begin_ts = booker.calc_begin_ts()
    dur_sec = booker.calc_duration()
    begin = datetime.datetime.fromtimestamp(begin_ts, CST)
    end = datetime.datetime.fromtimestamp(begin_ts + dur_sec, CST)
    return {
        "begin_ts": begin_ts,
        "duration": dur_sec,
        "begin_text": begin.strftime("%Y-%m-%d %H:%M"),
        "end_text": end.strftime("%H:%M"),
    }


def read_json_file(path):
    with open(abs_path(path), encoding="utf-8") as f:
        return json.load(f)


def write_json_file(path, data):
    with open(abs_path(path), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_index_html():
    with open(INDEX_FILE, encoding="utf-8") as f:
        return f.read()


def clean_booking_json(data):
    if isinstance(data, dict) and isinstance(data.get("booking"), dict):
        data["booking"].pop("begin_minute", None)
    return data


def run_python(args, timeout=180):
    proc = subprocess.run(
        [sys.executable] + args,
        cwd=SCRIPT_DIR,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    return {"returncode": proc.returncode, "output": proc.stdout}


def load_config():
    with open(DEFAULT_CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def trigger_time():
    cfg = load_config()
    return str((cfg.get("settings") or {}).get("trigger_time") or "20:00")


def timed_process_status():
    global TIMED_PROCESS
    status = {
        "running": False,
        "returncode": None,
        "pid": None,
        "log_file": TIMED_LOG_FILE,
        "log_tail": "",
    }
    if TIMED_PROCESS is not None:
        status["returncode"] = TIMED_PROCESS.poll()
        status["running"] = status["returncode"] is None
        status["pid"] = TIMED_PROCESS.pid
    if os.path.exists(TIMED_LOG_FILE):
        with open(TIMED_LOG_FILE, encoding="utf-8", errors="replace") as f:
            status["log_tail"] = f.read()[-5000:]
    return status


def start_timed_booking_process():
    global TIMED_PROCESS
    if TIMED_PROCESS is not None and TIMED_PROCESS.poll() is None:
        return timed_process_status()
    with open(TIMED_LOG_FILE, "a", encoding="utf-8", errors="replace") as log:
        log.write("\n\n=== start timed booking %s ===\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        TIMED_PROCESS = subprocess.Popen(
            [sys.executable, "book.py"],
            cwd=SCRIPT_DIR,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
    return timed_process_status()


def stop_timed_booking_process():
    global TIMED_PROCESS
    if TIMED_PROCESS is not None and TIMED_PROCESS.poll() is None:
        TIMED_PROCESS.terminate()
        try:
            TIMED_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            TIMED_PROCESS.kill()
    return timed_process_status()


def task_command():
    return f'"{sys.executable}" "{os.path.join(SCRIPT_DIR, "book.py")}" --now'


def windows_code_pages():
    if os.name != "nt":
        return []
    encodings = []
    try:
        encodings.append(f"cp{ctypes.windll.kernel32.GetOEMCP()}")
        encodings.append(f"cp{ctypes.windll.kernel32.GetACP()}")
    except Exception:
        pass
    return encodings


def decode_command_output(raw):
    encodings = ["utf-8-sig", locale.getpreferredencoding(False), sys.getfilesystemencoding()]
    encodings.extend(windows_code_pages())
    encodings.extend(["gbk", "cp936"])
    seen = set()
    for encoding in encodings:
        if not encoding:
            continue
        key = encoding.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode(locale.getpreferredencoding(False) or "utf-8", errors="replace")


def run_schtasks(args):
    proc = subprocess.run(
        ["schtasks"] + args,
        cwd=SCRIPT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    proc.stdout = decode_command_output(proc.stdout or b"")
    return proc


def install_daily_task():
    run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
    proc = run_schtasks(
        [
            "/Create",
            "/TN",
            TASK_NAME,
            "/TR",
            task_command(),
            "/SC",
            "DAILY",
            "/ST",
            trigger_time(),
            "/F",
        ]
    )
    return {"returncode": proc.returncode, "output": proc.stdout, "task_name": TASK_NAME, "trigger_time": trigger_time()}


def remove_daily_task():
    proc = run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
    return {"returncode": proc.returncode, "output": proc.stdout, "task_name": TASK_NAME}


def query_daily_task():
    proc = run_schtasks(["/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"])
    return {"returncode": proc.returncode, "output": proc.stdout, "task_name": TASK_NAME}


class Handler(BaseHTTPRequestHandler):
    server_version = "HDUBookWeb/1.0"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), fmt % args))

    def send_text(self, text, status=200, content_type="text/html; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw:
            return {}
        if "application/json" in (self.headers.get("Content-Type") or ""):
            return json.loads(raw)
        parsed = parse_qs(raw)
        return {k: v[-1] for k, v in parsed.items()}

    def safe_api(self, fn):
        try:
            self.send_json({"ok": True, "data": fn()})
        except Exception as e:
            self.send_json(
                {"ok": False, "error": str(e), "trace": traceback.format_exc(limit=3)},
                status=500,
            )

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self.send_text(load_index_html())
            return
        if path == "/api/status":
            self.safe_api(self.api_status)
            return
        if path == "/api/bookings":
            self.safe_api(lambda: make_booker().list_bookings(pages=1))
            return
        if path == "/api/booking-json":
            self.safe_api(lambda: read_json_file(DEFAULT_BOOKING_FILE))
            return
        if path == "/api/rooms":
            self.safe_api(lambda: STUDY_ROOM_NAMES)
            return
        if path == "/api/timed-status":
            self.safe_api(self.api_timed_status)
            return
        if path == "/api/daily-task":
            self.safe_api(lambda: {"trigger_time": trigger_time(), "task": query_daily_task()})
            return
        self.send_text("Not Found", status=404, content_type="text/plain; charset=utf-8")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/cancel":
            self.safe_api(self.api_cancel)
            return
        if path == "/api/save-booking-json":
            self.safe_api(self.api_save_booking_json)
            return
        if path == "/api/seat-status":
            self.safe_api(self.api_seat_status)
            return
        if path == "/api/dry-run":
            self.safe_api(self.api_dry_run)
            return
        if path == "/api/book-now":
            self.safe_api(lambda: run_python(["book.py", "--now"], timeout=240))
            return
        if path == "/api/start-timed-booking":
            self.safe_api(lambda: start_timed_booking_process())
            return
        if path == "/api/stop-timed-booking":
            self.safe_api(lambda: stop_timed_booking_process())
            return
        if path == "/api/install-daily-task":
            self.safe_api(lambda: install_daily_task())
            return
        if path == "/api/remove-daily-task":
            self.safe_api(lambda: remove_daily_task())
            return
        if path == "/api/test-book-cancel":
            body = self.read_body_json()
            wait = int(body.get("wait_seconds") or 20)
            self.safe_api(lambda: run_python(["test_book_cancel.py", "--wait-seconds", str(wait)], timeout=wait + 240))
            return
        self.send_text("Not Found", status=404, content_type="text/plain; charset=utf-8")

    def api_status(self):
        booker = make_booker()
        return {
            "login": True,
            "uid": booker.uid,
            "target": target_summary(booker),
            "booking_json": clean_booking_json(read_json_file(DEFAULT_BOOKING_FILE)),
            "rooms": STUDY_ROOM_NAMES,
            "default_room": DEFAULT_STUDY_ROOM_NAME,
            "trigger_time": trigger_time(),
            "timed_process": timed_process_status(),
        }

    def api_timed_status(self):
        return {
            "trigger_time": trigger_time(),
            "process": timed_process_status(),
            "task": query_daily_task(),
        }

    def api_cancel(self):
        body = self.read_body_json()
        booking_id = body.get("booking_id")
        ok, response = make_booker().cancel_booking(booking_id)
        return {"cancel_ok": ok, "response": response}

    def api_save_booking_json(self):
        body = self.read_body_json()
        data = body.get("json")
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            raise ValueError("booking.json 必须是 JSON 对象")
        data = clean_booking_json(data)
        write_json_file(DEFAULT_BOOKING_FILE, data)
        return read_json_file(DEFAULT_BOOKING_FILE)

    def api_seat_status(self):
        body = self.read_body_json()
        room_name = (body.get("room_name") or DEFAULT_STUDY_ROOM_NAME).strip()
        booker = make_booker()
        begin_ts = booker.calc_begin_ts()
        dur_sec = booker.calc_duration()
        rooms = booker.get_room_maps(begin_ts, dur_sec)
        selected = []
        for name, room in rooms.items():
            if room_name and room_name != name and room_name not in name:
                continue
            selected.append(room)
        if room_name and not selected:
            history_room = booker.get_room_map_from_booking_history(room_name)
            if history_room:
                selected.append(history_room)
        return {"target": target_summary(booker), "rooms": selected}

    def api_dry_run(self):
        booker = make_booker()
        begin_ts = booker.calc_begin_ts()
        dur_sec = booker.calc_duration()
        rows = []
        for target in booker.get_candidate_seats():
            try:
                seat = booker.resolve_seat(target, begin_ts, dur_sec)
                rows.append(
                    {
                        "ok": True,
                        "room_name": target["room_name"],
                        "seat_num": target["seat_num"],
                        "seat_id": seat.get("seat_id"),
                        "source": seat.get("source") or "current/history",
                    }
                )
            except Exception as e:
                rows.append(
                    {
                        "ok": False,
                        "room_name": target["room_name"],
                        "seat_num": target["seat_num"],
                        "error": str(e),
                    }
                )
        return {"target": target_summary(booker), "rows": rows}


def main():
    port = int(os.environ.get("HDU_BOOK_WEB_PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"本地网页已启动：http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()


