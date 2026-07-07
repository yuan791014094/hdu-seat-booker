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
            self.send_text(INDEX_HTML)
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


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>杭电自习室抢座</title>
  <style>
    :root { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; color: #1f2937; background: #f4f6f9; }
    body { margin: 0; }
    main { max-width: 1280px; margin: 0 auto; padding: 22px; }
    h1 { margin: 0; font-size: 28px; line-height: 1.2; }
    h2 { margin: 0 0 12px; font-size: 17px; }
    h3 { margin: 16px 0 10px; font-size: 15px; }
    section { background: #fff; border: 1px solid #dfe5ee; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
    button { border: 1px solid #b9c4d1; background: #fff; border-radius: 6px; padding: 8px 12px; cursor: pointer; margin: 0 8px 8px 0; min-height: 36px; }
    button.primary { background: #126f85; border-color: #126f85; color: #fff; }
    button.danger { background: #b42318; border-color: #b42318; color: #fff; }
    button:disabled { opacity: .45; cursor: not-allowed; }
    input, select { border: 1px solid #cad3df; border-radius: 6px; padding: 8px 10px; min-height: 36px; box-sizing: border-box; background: #fff; }
    select { min-width: 240px; margin: 0 8px 8px 0; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { border-bottom: 1px solid #e5eaf0; padding: 8px; text-align: left; }
    textarea { width: 100%; min-height: 330px; box-sizing: border-box; font-family: Consolas, monospace; font-size: 14px; border: 1px solid #cad3df; border-radius: 6px; padding: 10px; resize: vertical; }
    pre { white-space: pre-wrap; word-break: break-word; background: #101827; color: #e5edf8; border-radius: 8px; padding: 12px; min-height: 330px; max-height: 520px; overflow: auto; box-sizing: border-box; margin: 0; }
    .pageHead { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 16px; }
    .pageActions { flex: 0 0 auto; text-align: right; }
    .statusLine { margin-top: 6px; color: #667085; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .topGrid { display: grid; grid-template-columns: minmax(0, 1.08fr) minmax(360px, .92fr); gap: 16px; align-items: stretch; }
    .workPanel { display: flex; flex-direction: column; gap: 10px; }
    .compactPanel { padding-bottom: 8px; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 10px; }
    .toolbar button, .toolbar select, .toolbar input { margin: 0; }
    .helpLine { margin: 0 0 10px; line-height: 1.6; }
    .muted { color: #667085; }
    .pill { display: inline-block; border-radius: 999px; padding: 2px 8px; background: #e8f3f5; color: #126f85; font-size: 12px; }
    .mapMeta { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; margin: 8px 0; }
    .checkboxLine { display: inline-flex; align-items: center; gap: 6px; }
    .mapPick { background: #f1f5f9; border: 1px solid #dfe5ee; border-radius: 6px; padding: 8px 10px; margin: 8px 0 10px; min-height: 20px; }
    .mapWrap { overflow: auto; border: 1px solid #e5eaf0; border-radius: 8px; background: #f8fafc; margin-top: 12px; padding: 10px; }
    .mapWrap svg { display: block; width: 100%; min-width: 900px; height: auto; background: #fff; border-radius: 6px; }
    .seatNode rect { fill: rgba(18, 111, 133, .86); stroke: #fff; stroke-width: .28; vector-effect: non-scaling-stroke; cursor: pointer; }
    .seatNode:hover rect, .seatNode.active rect { fill: #b42318; stroke: #111827; }
    .seatNode text { display: none; pointer-events: none; font-family: Arial, sans-serif; font-weight: 700; fill: #111827; paint-order: stroke; stroke: #fff; stroke-width: .35; }
    .mapWrap.showLabels .seatNode text, .seatNode:hover text, .seatNode.active text { display: block; }
    .seatLayout { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(300px, .65fr); gap: 14px; align-items: start; }
    .seatTable { max-height: 620px; overflow: auto; border: 1px solid #e5eaf0; border-radius: 8px; margin-top: 12px; }
    .seatTable h3 { margin-left: 10px; }
    @media (max-width: 900px) { .grid, .topGrid, .seatLayout { grid-template-columns: 1fr; } .pageHead { display: block; } .pageActions { text-align: left; margin-top: 12px; } main { padding: 14px; } pre, textarea { min-height: 260px; } }
  </style>
</head>
<body>
<main>
  <div class="pageHead">
    <div>
      <h1>杭电自习室抢座</h1>
      <div id="status" class="statusLine">加载中...</div>
    </div>
    <div class="pageActions">
      <button onclick="loadAll()">刷新页面数据</button>
    </div>
  </div>

  <div class="topGrid">
    <section class="workPanel">
      <h2>booking.json</h2>
      <p class="helpLine muted">
        `room_name` 选自下方房间名；`seat_num` 填图上显示的座位号，例如 `395`，不是 `seat_id`。
      </p>
      <div id="bookingHelp" class="muted"></div>
      <div class="toolbar">
        <select id="candidateRoom"></select>
        <input id="candidateSeat" placeholder="显示座位号，例如 395">
        <button onclick="addCandidateSeat()">添加候选座位</button>
      </div>
      <textarea id="bookingJson"></textarea>
      <div class="toolbar">
        <button class="primary" onclick="saveBookingJson()">保存 booking.json</button>
        <button class="primary" onclick="dryRun()">验证座位配置</button>
        <button class="primary" onclick="bookNow()">立即按 JSON 预约</button>
        <button onclick="testBookCancel()">20 秒后测试预约并取消</button>
      </div>
    </section>

    <section class="workPanel">
      <h2>输出</h2>
      <pre id="output">准备好了。</pre>
    </section>
  </div>

  <div class="grid">
    <section>
      <h2>我的预约</h2>
      <button onclick="loadBookings()">刷新预约</button>
      <div id="bookings"></div>
    </section>
    <section>
      <h2>定时抢座</h2>
      <div id="timedStatus" class="muted">加载中...</div>
      <div class="toolbar">
        <button class="primary" onclick="startTimedBooking()">启动本次定时抢座</button>
        <button onclick="stopTimedBooking()">停止本次定时抢座</button>
        <button onclick="installDailyTask()">安装每日自动抢座</button>
        <button class="danger" onclick="removeDailyTask()">删除每日自动抢座</button>
      </div>
    </section>
  </div>

  <section>
    <h2>座位和平面图</h2>
    <div class="toolbar">
      <select id="roomSelect"></select>
      <button class="primary" onclick="loadSeatStatus()">读取座位和平面图</button>
    </div>
    <p class="helpLine muted">用于查显示座位号和实际 `seat_id`。宋韵云图默认使用四楼静态平面图；点击图上的座位点会把信息同步到输出。</p>
    <div class="seatLayout">
      <div id="roomMap" class="muted">选择房间后点击“读取座位和平面图”。</div>
      <div id="seatStatus"></div>
    </div>
  </section>
</main>
<script>
const $ = id => document.getElementById(id);
let defaultRoomName = "宋韵云图（四楼）";
function out(value) {
  $("output").textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
async function api(path, options = {}) {
  const res = await fetch(path, options);
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || "请求失败");
  return data.data;
}
async function loadStatus() {
  const data = await api("/api/status");
  defaultRoomName = data.default_room || defaultRoomName;
  $("status").innerHTML = `已登录 uid=<span class="pill">${data.uid}</span>，目标时段：<b>${data.target.begin_text} ~ ${data.target.end_text}</b>`;
  $("bookingJson").value = JSON.stringify(data.booking_json, null, 2);
  renderRoomOptions(data.rooms || []);
  renderTimedStatus({trigger_time:data.trigger_time, process:data.timed_process});
}
function renderRoomOptions(rooms) {
  const defaultOption = rooms.includes(defaultRoomName) ? defaultRoomName : (rooms[0] || "");
  const prev = $("roomSelect").value || defaultOption;
  $("roomSelect").innerHTML = rooms.map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("") + `<option value="">全部自习室</option>`;
  $("roomSelect").value = prev;
  const candidatePrev = $("candidateRoom").value || defaultOption;
  $("candidateRoom").innerHTML = rooms.map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("");
  $("candidateRoom").value = candidatePrev;
  $("bookingHelp").innerHTML = `默认围绕 <span class="pill">${escapeHtml(defaultRoomName)}</span>。可用 room_name：${rooms.map(name => `<span class="pill">${escapeHtml(name)}</span>`).join(" ")}`;
}
async function loadBookings() {
  const rows = await api("/api/bookings");
  if (!rows.length) {
    $("bookings").innerHTML = "<p class='muted'>暂无预约</p>";
    return;
  }
  $("bookings").innerHTML = `<table><thead><tr><th>ID</th><th>座位</th><th>时间</th><th>状态</th><th></th></tr></thead><tbody>` +
    rows.map(r => `<tr><td>${r.booking_id}</td><td>${escapeHtml(r.room_name)} ${escapeHtml(r.seat_num)}</td><td>${r.begin_text} ~ ${r.end_text}</td><td>${r.status_text}</td><td>${r.can_cancel ? `<button class="danger" onclick="cancelBooking('${r.booking_id}')">取消</button>` : ""}</td></tr>`).join("") +
    `</tbody></table>`;
}
async function loadAll() {
  try { await loadStatus(); await loadBookings(); await loadTimedStatus(false); out("已刷新"); } catch (e) { out(e.message); }
}
async function cancelBooking(id) {
  if (!confirm(`确定取消预约 ${id}？`)) return;
  try { out(await api("/api/cancel", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({booking_id:id})})); await loadBookings(); } catch (e) { out(e.message); }
}
async function saveBookingJson() {
  try {
    JSON.parse($("bookingJson").value);
    out(await api("/api/save-booking-json", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({json:$("bookingJson").value})}));
    await loadStatus();
  } catch (e) { out(e.message); }
}
function addCandidateSeat() {
  try {
    const data = JSON.parse($("bookingJson").value);
    const room = $("candidateRoom").value;
    const seat = $("candidateSeat").value.trim();
    if (!room) throw new Error("请先选择 room_name");
    if (!seat) throw new Error("请填写 seat_num，例如 395");
    if (!Array.isArray(data.seats_priority)) data.seats_priority = [];
    data.seats_priority.push({room_name: room, seat_num: seat});
    $("bookingJson").value = JSON.stringify(data, null, 2);
    out(`已添加：${room} ${seat}。确认无误后点“保存 booking.json”。`);
  } catch (e) { out(e.message); }
}
async function loadSeatStatus() {
  try {
    const data = await api("/api/seat-status", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({room_name:$("roomSelect").value})});
    renderSeatStatus(data);
    out({target:data.target, rooms:data.rooms.map(r => ({room_name:r.room_name, seats:r.seats.length, source:r.source || "current"}))});
  } catch (e) { out(e.message); }
}
function renderSeatStatus(data) {
  if (!data.rooms.length) {
    $("roomMap").textContent = "当前时段系统没有返回这个房间，也没有找到可复用的历史平面图。";
    $("seatStatus").innerHTML = "";
    return;
  }
  $("roomMap").innerHTML = data.rooms.map((room, idx) => renderMap(room, idx)).join("");
  $("seatStatus").innerHTML = data.rooms.map(room => `<h3>${escapeHtml(room.room_name)}</h3><div class="seatTable"><table><thead><tr><th>显示座位号</th><th>实际 seat_id</th></tr></thead><tbody>` +
    room.seats.map(s => `<tr><td>${escapeHtml(s.seat_num)}</td><td>${escapeHtml(s.seat_id)}</td></tr>`).join("") +
    `</tbody></table></div>`).join("");
  bindSeatMapClicks();
}
function renderMap(room, idx) {
  const info = room.info || {};
  const width = Number(info.width || 160);
  const height = Number(info.height || 120);
  const img = info.plan ? `<image href="${escapeAttr(info.plan)}" x="0" y="0" width="${width}" height="${height}" preserveAspectRatio="none" opacity="0.92"></image>` : `<rect x="0" y="0" width="${width}" height="${height}" fill="#eef2f6"></rect>`;
  const seats = room.seats.map(s => {
    const rawX = Number(s.x || 0), rawY = Number(s.y || 0), rawW = Number(s.w || 2), rawH = Number(s.h || 2);
    const w = Math.max(rawW, 1.35), h = Math.max(rawH, 1.35);
    const x = rawX + (rawW - w) / 2, y = rawY + (rawH - h) / 2;
    const fs = Math.max(1.7, Math.min(2.2, w * 1.05));
    return `<g class="seatNode" tabindex="0" data-room="${escapeAttr(room.room_name)}" data-seat-num="${escapeAttr(s.seat_num)}" data-seat-id="${escapeAttr(s.seat_id)}"><title>${escapeHtml(s.seat_num)} / ${escapeHtml(s.seat_id)}</title><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="0.25"></rect><text x="${x + w / 2}" y="${y + h / 2 + fs / 3}" text-anchor="middle" font-size="${fs}">${escapeHtml(s.seat_num)}</text></g>`;
  }).join("");
  const sourceText = room.source === "history" ? `历史预约平面图，来自 bookingId ${escapeHtml(room.history_booking_id || "")}，只用于核对座位号和 seat_id` : "当前查询接口返回的座位图";
  return `<h3>${escapeHtml(room.room_name)}</h3><div class="mapMeta"><span class="pill">${escapeHtml(sourceText)}</span><span>${room.seats.length} 个座位点</span><label class="checkboxLine"><input type="checkbox" onchange="toggleSeatLabels(this, 'map-${idx}')">显示座位号</label></div><div id="pick-${idx}" class="mapPick muted">点击图上的座位点查看显示号和实际 seat_id。</div><div id="map-${idx}" class="mapWrap"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeAttr(room.room_name)}">${img}${seats}</svg></div>`;
}
function bindSeatMapClicks() {
  document.querySelectorAll(".seatNode").forEach(node => {
    const show = () => {
      document.querySelectorAll(".seatNode.active").forEach(item => item.classList.remove("active"));
      node.classList.add("active");
      const mapWrap = node.closest(".mapWrap");
      const pick = mapWrap ? document.getElementById(mapWrap.id.replace("map-", "pick-")) : null;
      const text = `${node.dataset.room}：显示座位号 ${node.dataset.seatNum}，实际 seat_id ${node.dataset.seatId}`;
      if (pick) {
        pick.className = "mapPick";
        pick.textContent = text;
      }
      out(text);
    };
    node.addEventListener("click", show);
    node.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        show();
      }
    });
  });
}
function toggleSeatLabels(input, mapId) {
  const map = document.getElementById(mapId);
  if (map) map.classList.toggle("showLabels", input.checked);
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}
async function dryRun() {
  try {
    const data = await api("/api/dry-run", {method:"POST"});
    const lines = [
      `验证时段：${data.target.begin_text} ~ ${data.target.end_text}`,
      ""
    ];
    for (const row of data.rows) {
      if (row.ok) {
        lines.push(`通过  ${row.room_name}  显示座位号 ${row.seat_num}  -> seat_id ${row.seat_id}`);
      } else {
        lines.push(`失败  ${row.room_name}  显示座位号 ${row.seat_num}  -> ${row.error}`);
      }
    }
    out(lines.join("\n"));
  } catch (e) { out(e.message); }
}
async function bookNow() {
  if (!confirm("确定现在提交预约？")) return;
  try { out(await api("/api/book-now", {method:"POST"})); await loadBookings(); } catch (e) { out(e.message); }
}
async function loadTimedStatus(showOutput = true) {
  try {
    const data = await api("/api/timed-status");
    renderTimedStatus(data);
    if (showOutput) out(data);
  } catch (e) { out(e.message); }
}
function renderTimedStatus(data) {
  const process = data.process || {};
  const task = data.task || {};
  const taskInstalled = task.returncode === 0;
  const lines = [
    `定时时间：${escapeHtml(data.trigger_time || "20:00")}`,
    `本次定时进程：${process.running ? "运行中，等待到点抢座" : "未运行"}`,
    `每日自动任务：${taskInstalled ? "已安装" : "未安装或无法查询"}`
  ];
  if (process.pid) lines.push(`进程 PID：${process.pid}`);
  if (process.log_tail) lines.push(`<br><pre>${escapeHtml(process.log_tail)}</pre>`);
  $("timedStatus").innerHTML = lines.join("<br>");
}
async function startTimedBooking() {
  if (!confirm("启动后，网页服务会开一个后台进程等待到 20:00 再抢座。电脑和网页服务需要保持运行。继续？")) return;
  try { out(await api("/api/start-timed-booking", {method:"POST"})); await loadTimedStatus(false); } catch (e) { out(e.message); }
}
async function stopTimedBooking() {
  try { out(await api("/api/stop-timed-booking", {method:"POST"})); await loadTimedStatus(false); } catch (e) { out(e.message); }
}
async function installDailyTask() {
  if (!confirm("这会安装 Windows 每日自动任务，每天到 config.yaml 的 trigger_time 自动运行抢座。继续？")) return;
  try { out(await api("/api/install-daily-task", {method:"POST"})); await loadTimedStatus(false); } catch (e) { out(e.message); }
}
async function removeDailyTask() {
  if (!confirm("确定删除 Windows 每日自动抢座任务？")) return;
  try { out(await api("/api/remove-daily-task", {method:"POST"})); await loadTimedStatus(false); } catch (e) { out(e.message); }
}
async function testBookCancel() {
  if (!confirm("测试会等待 20 秒，预约成功后立刻取消。继续？")) return;
  try { out("测试运行中，请等输出返回..."); out(await api("/api/test-book-cancel", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({wait_seconds:20})})); await loadBookings(); } catch (e) { out(e.message); }
}
loadAll();
</script>
</body>
</html>
"""


def main():
    port = int(os.environ.get("HDU_BOOK_WEB_PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"本地网页已启动：http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
