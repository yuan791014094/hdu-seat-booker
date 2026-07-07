# -*- coding: utf-8 -*-
"""
HDU library study-room seat booking.

Login: Selenium for CAS when cookies are missing or expired.
Booking: requests with saved cookies.
"""
import io
import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import argparse
import base64
import datetime
import hashlib
import json
import re
import time
from urllib.parse import urlparse

import requests
import yaml

CST = datetime.timezone(datetime.timedelta(hours=8), "CST")
BASE = "https://hdu.huitu.zhishulib.com"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.yaml")
DEFAULT_BOOKING_FILE = os.path.join(SCRIPT_DIR, "booking.json")
DEFAULT_COOKIE_FILE = os.path.join(SCRIPT_DIR, "cookies.json")

STUDY_ROOM_CATEGORY_ID = "591"
STUDY_ROOM_CONTENT_ID = "3"
DEFAULT_STUDY_ROOM_NAME = "宋韵云图（四楼）"

STUDY_ROOM_NAMES = [
    DEFAULT_STUDY_ROOM_NAME,
    "杭韵数阁（六楼）",
    "格物E堂（二楼东）",
    "数智渊阁（二楼 信息检索室）",
    "芯灵驿站（十二楼）",
    "比特庭园（二楼西）",
]

BOOKING_STATUS_TEXT = {
    "0": "未开始",
    "1": "使用中",
    "2": "已结束",
    "3": "已爽约",
    "4": "已取消",
    "5": "已过期",
    "6": "已签退",
    "7": "已结束",
}

SEAT_STATE_TEXT = {
    "0": "可预约",
    "1": "不可预约",
    "2": "不可预约",
    "3": "不可预约",
}
STATIC_SEAT_STATE_TEXT = "静态平面图"


def now_cst():
    return datetime.datetime.now(CST)


def cst_timestamp(day, hour, minute=0):
    dt = datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=CST)
    return int(dt.timestamp())


def log(msg):
    print(f"[{now_cst().strftime('%H:%M:%S')} CST] {msg}")


def abs_path(path):
    return path if os.path.isabs(path) else os.path.join(SCRIPT_DIR, path)


def normalize_text(value):
    return str(value or "").strip()


def normalize_seat_num(value):
    value = normalize_text(value)
    if value.isdigit():
        return str(int(value))
    return value


def parse_date_token(value):
    value = normalize_text(value)
    if value in ("auto", "release_auto"):
        base = now_cst()
        release = base.replace(hour=20, minute=0, second=0, microsecond=0)
        days = 2 if base >= release else 1
        return base.date() + datetime.timedelta(days=days)
    if value == "today":
        return now_cst().date()
    if value == "tomorrow" or not value:
        return now_cst().date() + datetime.timedelta(days=1)
    if value in ("day_after_tomorrow", "after_tomorrow", "after_after_tomorrow"):
        return now_cst().date() + datetime.timedelta(days=2)
    return datetime.date.fromisoformat(value)


def sorted_seats(seats):
    def key(item):
        title = normalize_text(item.get("seat_num") or item.get("title"))
        return (0, int(title)) if title.isdigit() else (1, title)

    return sorted(seats, key=key)


def apply_cookie_list(session, cookies):
    session.cookies.clear()
    for c in cookies or []:
        name = c.get("name")
        if not name:
            continue
        kwargs = {}
        if c.get("domain"):
            kwargs["domain"] = c["domain"]
        if c.get("path"):
            kwargs["path"] = c["path"]
        if c.get("secure") is not None:
            kwargs["secure"] = c["secure"]
        if c.get("expires") is not None:
            kwargs["expires"] = c["expires"]
        if c.get("expiry") is not None:
            kwargs["expires"] = c["expiry"]
        session.cookies.set(name, c.get("value", ""), **kwargs)


def safe_json_dict(resp, context):
    try:
        data = resp.json()
    except ValueError:
        snippet = getattr(resp, "text", "")[:160]
        log(f"{context} 不是 JSON: {snippet}")
        return None
    if not isinstance(data, dict):
        log(f"{context} 返回结构异常: {type(data).__name__}")
        return None
    return data


def find_first_key(node, wanted_keys):
    wanted = {k.lower() for k in wanted_keys}
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in wanted and value not in (None, ""):
                return value
        for value in node.values():
            found = find_first_key(value, wanted)
            if found not in (None, ""):
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_first_key(item, wanted)
            if found not in (None, ""):
                return found
    return None


def format_cst_ts(value, fmt="%Y-%m-%d %H:%M"):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return datetime.datetime.fromtimestamp(value, CST).strftime(fmt)


def selenium_login(username, password, headless=True):
    """Use Selenium to finish CAS login and return browser cookies."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    else:
        opts.add_experimental_option("detach", True)
        opts.add_argument("--start-maximized")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")

    driver = webdriver.Chrome(options=opts)
    if not headless:
        try:
            driver.maximize_window()
        except Exception:
            pass
    wait = WebDriverWait(driver, 30)
    lib_host = urlparse(BASE).netloc

    def visible_input(predicate):
        for item in driver.find_elements(By.TAG_NAME, "input"):
            if item.is_displayed() and predicate(item):
                return item
        return None

    def enabled_login_button(d):
        for btn in d.find_elements(By.CSS_SELECTOR, "button.login-button, button[type=submit]"):
            cls = btn.get_attribute("class") or ""
            if btn.is_displayed() and "disabled" not in cls:
                return btn
        return None

    def visible_captcha():
        return visible_input(
            lambda i: i.get_attribute("name") == "captcha_code"
            and i.get_attribute("type") != "hidden"
        )

    def page_text():
        try:
            return driver.find_element(By.TAG_NAME, "body").text.replace("\n", " ")[:300]
        except Exception:
            return ""

    try:
        target = f"{BASE}/User/Index/hduCASLogin?forward=%2FSpace%2FCategory%2Flist"
        cas_url = f"https://sso.hdu.edu.cn/login?service={requests.utils.quote(target, safe='')}"
        driver.get(cas_url)

        if not headless:
            log("浏览器已打开，请在窗口中手动登录；登录后回到终端按回车继续。")
            try:
                input("")
            except EOFError:
                pass
            deadline = time.time() + 180
            while time.time() < deadline:
                if urlparse(driver.current_url).netloc == lib_host:
                    time.sleep(1)
                    cookies = driver.get_cookies()
                    log(f"登录成功，获取到 {len(cookies)} 个 cookie")
                    return cookies
                time.sleep(1)
            raise RuntimeError("手动登录后未跳转到图书馆页面")

        wait.until(lambda d: visible_input(lambda i: i.get_attribute("type") == "password"))
        time.sleep(0.3)
        un_box = visible_input(lambda i: i.get_attribute("name") == "username")
        pw_box = visible_input(lambda i: i.get_attribute("type") == "password")
        if not un_box or not pw_box:
            raise RuntimeError("SSO 页面未找到可见的账号或密码输入框")

        un_box.clear()
        un_box.click()
        un_box.send_keys(username)
        pw_box.clear()
        pw_box.click()
        pw_box.send_keys(password)

        for _ in range(3):
            btn = wait.until(enabled_login_button)
            ActionChains(driver).move_to_element(btn).pause(0.2).click().perform()

            deadline = time.time() + 45
            while time.time() < deadline:
                if urlparse(driver.current_url).netloc == lib_host:
                    time.sleep(1)
                    cookies = driver.get_cookies()
                    log(f"登录成功，获取到 {len(cookies)} 个 cookie")
                    return cookies

                captcha_box = visible_captcha()
                if captcha_box:
                    captcha_path = os.path.abspath("captcha.png")
                    driver.save_screenshot(captcha_path)
                    print(f"SSO 需要验证码，已保存截图：{captcha_path}")
                    try:
                        code = input("请输入验证码后回车：").strip()
                    except EOFError:
                        raise RuntimeError(
                            f"SSO 需要验证码；请打开 {captcha_path} 查看后在交互式终端运行"
                        ) from None
                    if not code:
                        raise RuntimeError("验证码为空，已取消登录")
                    captcha_box.clear()
                    captcha_box.send_keys(code)
                    break

                time.sleep(0.5)
            else:
                break

        raise RuntimeError(f"SSO 登录未跳转到图书馆站点，当前页面：{page_text()}")
    finally:
        driver.quit()


class LibraryBooker:
    def __init__(
        self,
        cfg_path=DEFAULT_CONFIG_FILE,
        booking_path=DEFAULT_BOOKING_FILE,
        headless=True,
        cookie_file=DEFAULT_COOKIE_FILE,
        refresh_cookie=False,
    ):
        self.cfg_path = abs_path(cfg_path)
        self.booking_path = abs_path(booking_path) if booking_path else None
        with open(self.cfg_path, encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f) or {}

        if os.environ.get("HDU_USERNAME"):
            self.cfg.setdefault("account", {})["username"] = os.environ["HDU_USERNAME"]
        if os.environ.get("HDU_PASSWORD"):
            self.cfg.setdefault("account", {})["password"] = os.environ["HDU_PASSWORD"]

        self.booking_json = self.load_booking_json()
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        self.uid = None
        self.headless = headless
        self.refresh_cookie = refresh_cookie
        self.cookie_file = abs_path(cookie_file)

    def load_booking_json(self):
        if not self.booking_path or not os.path.exists(self.booking_path):
            return {}
        with open(self.booking_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"{self.booking_path} 顶层必须是 JSON 对象")
        return data

    def load_cookie_cache(self):
        if self.refresh_cookie or not os.path.exists(self.cookie_file):
            return []
        try:
            with open(self.cookie_file, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data.get("cookies", [])
            if isinstance(data, list):
                return data
        except Exception as e:
            log(f"读取 cookie 缓存失败: {e}")
        return []

    def save_cookie_cache(self, cookies):
        try:
            payload = {"saved_at": now_cst().isoformat(), "cookies": cookies}
            with open(self.cookie_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            log(f"已保存 cookie 到 {self.cookie_file}")
        except Exception as e:
            log(f"保存 cookie 失败: {e}")

    def verify_login(self):
        r = self.session.get(
            f"{BASE}/Seat/Index/searchSeats?LAB_JSON=1"
            f"&space_category[category_id]={STUDY_ROOM_CATEGORY_ID}"
            f"&space_category[content_id]={STUDY_ROOM_CONTENT_ID}",
            timeout=10,
        )
        resp = safe_json_dict(r, "登录验证")
        if not resp or resp.get("ui_type") == "com.Redirect":
            return False
        data = resp.get("data")
        if isinstance(data, dict) and data.get("is_login"):
            self.uid = data["uid"]
            log(f"uid = {self.uid}")
            return True
        return False

    def login(self):
        cached_cookies = self.load_cookie_cache()
        if cached_cookies:
            apply_cookie_list(self.session, cached_cookies)
            if self.verify_login():
                log("复用已保存的 cookie 登录成功")
                return True
            log("已保存的 cookie 失效，改为重新登录")

        acc = self.cfg.get("account", {})
        try:
            cookies = selenium_login(acc.get("username", ""), acc.get("password", ""), self.headless)
        except Exception as e:
            log(f"登录失败: {e}")
            return False
        apply_cookie_list(self.session, cookies)
        if self.verify_login():
            self.save_cookie_cache(cookies)
            return True
        log("登录验证失败")
        return False

    def booking_config(self):
        merged = dict(self.cfg.get("booking", {}))
        merged.update(self.booking_json.get("booking", {}))
        return merged

    def calc_begin_ts(self):
        b = self.booking_config()
        day = parse_date_token(b.get("date", "auto"))
        begin_hour = int(b.get("begin_hour", 8))
        return cst_timestamp(day, begin_hour, 0)

    def calc_duration(self):
        b = self.booking_config()
        dur = b.get("duration_hours")
        if dur not in (None, ""):
            return int(dur) * 3600
        begin_hour = int(b.get("begin_hour", 8))
        return max(1, 22 - begin_hour) * 3600

    def search_payload(self, begin_ts, dur_sec, num=1):
        return {
            "space_category[category_id]": STUDY_ROOM_CATEGORY_ID,
            "space_category[content_id]": STUDY_ROOM_CONTENT_ID,
            "beginTime": begin_ts,
            "duration": dur_sec,
            "num": num,
        }

    def search_seats_raw(self, begin_ts, dur_sec, num=1):
        resp = self.session.post(
            f"{BASE}/Seat/Index/searchSeats?LAB_JSON=1",
            data=self.search_payload(begin_ts, dur_sec, num=num),
            timeout=15,
        )
        return safe_json_dict(resp, f"查询自习室 num={num}")

    def iter_recommend_items(self, node):
        if isinstance(node, dict):
            if node.get("ui_type") == "ht.Seat.RecommendSeatItem" and isinstance(node.get("seatMap"), dict):
                yield node
            for child in node.values():
                yield from self.iter_recommend_items(child)
        elif isinstance(node, list):
            for child in node:
                yield from self.iter_recommend_items(child)

    def iter_study_room_seats(self, begin_ts, dur_sec, max_pages=8):
        seen_rooms = set()
        seen_seats = set()
        for num in range(1, max_pages + 1):
            data = self.search_seats_raw(begin_ts, dur_sec, num=num)
            if not data:
                continue
            if data.get("ui_type") == "com.Message":
                break

            root = data.get("allContent") or data.get("content") or data
            found_on_page = 0
            for item in self.iter_recommend_items(root):
                room_name = normalize_text(item.get("roomName"))
                seat_map = item.get("seatMap") or {}
                pois = seat_map.get("POIs")
                if not isinstance(pois, list):
                    continue
                seen_rooms.add(room_name)
                found_on_page += 1
                for poi in pois:
                    if not isinstance(poi, dict):
                        continue
                    seat_id = normalize_text(poi.get("id"))
                    seat_num = normalize_seat_num(poi.get("title"))
                    if not seat_id or not seat_num:
                        continue
                    key = (room_name, seat_num, seat_id)
                    if key in seen_seats:
                        continue
                    seen_seats.add(key)
                    yield {
                        "room_name": room_name,
                        "seat_num": seat_num,
                        "seat_id": seat_id,
                        "category_id": normalize_text(poi.get("category_id")),
                        "state": normalize_text(poi.get("state")),
                        "state_text": SEAT_STATE_TEXT.get(normalize_text(poi.get("state")), "未知"),
                        "raw": poi,
                    }

            if len(seen_rooms) >= len(STUDY_ROOM_NAMES) and found_on_page == 0:
                break

    def group_study_room_seats(self, begin_ts, dur_sec):
        grouped = {name: [] for name in STUDY_ROOM_NAMES}
        for seat in self.iter_study_room_seats(begin_ts, dur_sec):
            grouped.setdefault(seat["room_name"], []).append(seat)
        return {room: sorted_seats(seats) for room, seats in grouped.items() if seats}

    def get_room_maps(self, begin_ts, dur_sec, max_pages=8):
        rooms = {}
        for num in range(1, max_pages + 1):
            data = self.search_seats_raw(begin_ts, dur_sec, num=num)
            if not data or data.get("ui_type") == "com.Message":
                break
            root = data.get("allContent") or data.get("content") or data
            for item in self.iter_recommend_items(root):
                room_name = normalize_text(item.get("roomName"))
                if not room_name:
                    continue
                seat_map = item.get("seatMap") or {}
                pois = seat_map.get("POIs")
                if not isinstance(pois, list):
                    continue
                room = rooms.setdefault(
                    room_name,
                    {
                        "room_name": room_name,
                        "info": seat_map.get("info") or {},
                        "seats": [],
                        "source": "current",
                    },
                )
                seen = {seat["seat_id"] for seat in room["seats"]}
                for poi in pois:
                    if not isinstance(poi, dict):
                        continue
                    seat_id = normalize_text(poi.get("id"))
                    seat_num = normalize_seat_num(poi.get("title"))
                    if not seat_id or not seat_num or seat_id in seen:
                        continue
                    seen.add(seat_id)
                    room["seats"].append(
                        {
                            "room_name": room_name,
                            "seat_num": seat_num,
                            "seat_id": seat_id,
                            "category_id": normalize_text(poi.get("category_id")),
                            "state": normalize_text(poi.get("state")),
                            "state_text": SEAT_STATE_TEXT.get(normalize_text(poi.get("state")), "未知"),
                            "x": float(poi.get("x") or 0),
                            "y": float(poi.get("y") or 0),
                            "w": float(poi.get("w") or 2),
                            "h": float(poi.get("h") or 2),
                            "raw": poi,
                        }
                    )
                room["seats"] = sorted_seats(room["seats"])
            if len(rooms) >= len(STUDY_ROOM_NAMES):
                break
        return rooms

    def iter_seat_maps(self, node, seen=None):
        if seen is None:
            seen = set()
        if isinstance(node, dict):
            node_id = id(node)
            if node_id in seen:
                return
            seen.add(node_id)
            if isinstance(node.get("seatMap"), dict):
                yield node["seatMap"]
            if isinstance(node.get("POIs"), list):
                yield node
            for child in node.values():
                yield from self.iter_seat_maps(child, seen)
        elif isinstance(node, list):
            for child in node:
                yield from self.iter_seat_maps(child, seen)

    def normalize_room_map(self, seat_map, room_name="", source="current"):
        if not isinstance(seat_map, dict):
            return None
        booking = seat_map.get("booking") if isinstance(seat_map.get("booking"), dict) else {}
        space = booking.get("space") if isinstance(booking.get("space"), dict) else {}
        booked_seat = booking.get("seat") if isinstance(booking.get("seat"), dict) else {}
        booked_seat_space = booked_seat.get("space") if isinstance(booked_seat.get("space"), dict) else {}

        room_name = (
            normalize_text(room_name)
            or normalize_text(seat_map.get("roomName"))
            or normalize_text(space.get("title"))
            or normalize_text(booked_seat_space.get("format"))
        )
        if not room_name:
            return None

        info = seat_map.get("info") if isinstance(seat_map.get("info"), dict) else {}
        if not info and space:
            info = {
                "id": space.get("id"),
                "title": space.get("title"),
                "plan": space.get("plan"),
                "width": space.get("width"),
                "height": space.get("height"),
            }

        pois = seat_map.get("POIs")
        if not isinstance(pois, list):
            return None

        seats = []
        seen = set()
        for poi in pois:
            if not isinstance(poi, dict):
                continue
            seat_id = normalize_text(poi.get("id"))
            seat_num = normalize_seat_num(poi.get("title"))
            if not seat_id or not seat_num or seat_id in seen:
                continue
            seen.add(seat_id)
            state = normalize_text(poi.get("state"))
            seats.append(
                {
                    "room_name": room_name,
                    "seat_num": seat_num,
                    "seat_id": seat_id,
                    "category_id": normalize_text(poi.get("category_id")),
                    "state": state,
                    "state_text": SEAT_STATE_TEXT.get(state, STATIC_SEAT_STATE_TEXT if source != "current" else "未知"),
                    "x": float(poi.get("x") or 0),
                    "y": float(poi.get("y") or 0),
                    "w": float(poi.get("w") or 2),
                    "h": float(poi.get("h") or 2),
                    "raw": poi,
                }
            )

        if not seats:
            return None
        return {
            "room_name": room_name,
            "info": info,
            "seats": sorted_seats(seats),
            "source": source,
        }

    def booking_info_raw(self, booking_id):
        booking_id = normalize_text(booking_id)
        if not booking_id:
            return None
        try:
            r = self.session.get(
                f"{BASE}/Seat/Index/bookingInfo?bookingId={booking_id}&fromType=1&LAB_JSON=1",
                timeout=15,
            )
        except Exception as e:
            log(f"查询预约详情出错: {e}")
            return None
        return safe_json_dict(r, f"查询预约详情 bookingId={booking_id}")

    def get_room_map_from_booking_history(self, room_name=DEFAULT_STUDY_ROOM_NAME, pages=3):
        room_name = normalize_text(room_name) or DEFAULT_STUDY_ROOM_NAME
        for booking in self.list_bookings(pages=pages):
            booking_room = normalize_text(booking.get("room_name"))
            if room_name and room_name != booking_room and room_name not in booking_room:
                continue
            data = self.booking_info_raw(booking.get("booking_id"))
            if not data:
                continue
            for seat_map in self.iter_seat_maps(data):
                room = self.normalize_room_map(seat_map, source="history")
                if not room:
                    continue
                found_room = normalize_text(room.get("room_name"))
                if room_name and room_name != found_room and room_name not in found_room:
                    continue
                room["history_booking_id"] = booking.get("booking_id")
                return room
        return None

    def get_candidate_seats(self):
        data = self.booking_json if self.booking_json else self.cfg
        candidates = data.get("seats_priority")
        if candidates is None:
            candidates = data.get("seats")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("没有候选座位：请在 booking.json 里填写 seats_priority")

        normalized = []
        for idx, seat in enumerate(candidates, 1):
            if not isinstance(seat, dict):
                raise ValueError(f"第 {idx} 个座位配置必须是对象")
            room_name = normalize_text(seat.get("room_name"))
            seat_num = normalize_seat_num(seat.get("seat_num"))
            if not room_name or not seat_num:
                raise ValueError(f"第 {idx} 个座位缺少 room_name 或 seat_num")
            normalized.append({"room_name": room_name, "seat_num": seat_num})
        return normalized

    def resolve_seat(self, target, begin_ts, dur_sec):
        target_room = normalize_text(target.get("room_name"))
        target_num = normalize_seat_num(target.get("seat_num"))
        matches = []
        for seat in self.iter_study_room_seats(begin_ts, dur_sec):
            if seat["room_name"] == target_room and seat["seat_num"] == target_num:
                matches.append(seat)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(f"座位匹配到多个结果：{target_room} {target_num}")
        history_room = self.get_room_map_from_booking_history(target_room)
        if history_room:
            for seat in history_room["seats"]:
                if seat["seat_num"] == target_num:
                    log(f"使用历史平面图解析：{target_room} {target_num} -> seat_id={seat['seat_id']}")
                    return seat
        return None

    def book_seat_result(self, target, begin_ts, dur_sec):
        try:
            seat = self.resolve_seat(target, begin_ts, dur_sec)
        except Exception as e:
            log(f"查询座位信息失败: {e}")
            return {"ok": False, "seat": None, "response": None, "booking_id": None}
        if not seat:
            log(f"未找到座位：{target['room_name']} {target['seat_num']}")
            return {"ok": False, "seat": None, "response": None, "booking_id": None}

        seat_id = seat["seat_id"]
        api_time = int(time.time())
        raw = (
            "post&/Seat/Index/bookSeats?LAB_JSON=1"
            f"&api_time{api_time}&beginTime{begin_ts}"
            f"&duration{dur_sec}&is_recommend0"
            f"&seatBookers[0]{self.uid}&seats[0]{seat_id}"
        )
        token = base64.b64encode(hashlib.md5(raw.encode()).hexdigest().encode()).decode()

        payload = {
            "seats[0]": seat_id,
            "seatBookers[0]": self.uid,
            "beginTime": begin_ts,
            "duration": dur_sec,
            "is_recommend": 0,
            "api_time": api_time,
        }
        try:
            r = self.session.post(
                f"{BASE}/Seat/Index/bookSeats?LAB_JSON=1",
                data=payload,
                headers={"Api-Token": token},
                timeout=10,
            )
            d = safe_json_dict(r, "预约")
            if not d:
                return {"ok": False, "seat": seat, "response": None, "booking_id": None}
            code = d.get("CODE", "")
            data = d.get("DATA")
            msg = d.get("MESSAGE") or (data.get("msg", "") if isinstance(data, dict) else "")
            if code == "ok":
                log(f"预约成功：{seat['room_name']} {seat['seat_num']} (seat_id={seat_id}) | {msg}")
                booking_id = find_first_key(
                    d,
                    ["bookingId", "booking_id", "bookId", "book_id", "orderId", "order_id"],
                )
                return {"ok": True, "seat": seat, "response": d, "booking_id": booking_id}
            log(f"{seat['room_name']} {seat['seat_num']} (seat_id={seat_id}) -> [{code}] {msg}")
            return {"ok": False, "seat": seat, "response": d, "booking_id": None}
        except Exception as e:
            log(f"预约请求出错: {e}")
        return {"ok": False, "seat": None, "response": None, "booking_id": None}

    def book_seat(self, target, begin_ts, dur_sec):
        return bool(self.book_seat_result(target, begin_ts, dur_sec).get("ok"))

    def cancel_booking(self, booking_id):
        booking_id = normalize_text(booking_id)
        if not booking_id:
            log("取消失败：bookingId 为空")
            return False, None
        try:
            r = self.session.post(
                f"{BASE}/Seat/Index/cancelBooking?LAB_JSON=1",
                data={"bookingId": booking_id},
                timeout=10,
            )
            d = safe_json_dict(r, "取消预约")
            if not d:
                return False, None
            code = d.get("CODE", "")
            msg = d.get("MESSAGE", "")
            if code == "ok":
                log(f"取消预约成功：bookingId={booking_id} | {msg}")
                return True, d
            log(f"取消预约失败：bookingId={booking_id} -> [{code}] {msg}")
            return False, d
        except Exception as e:
            log(f"取消预约请求出错: {e}")
            return False, None

    def list_bookings(self, pages=1, only_active=False):
        bookings = []
        next_url = "/Seat/Index/myBookingList?LAB_JSON=1"
        seen_urls = set()

        for _ in range(max(1, int(pages))):
            if not next_url or next_url in seen_urls:
                break
            seen_urls.add(next_url)
            try:
                r = self.session.get(
                    f"{BASE}{next_url}",
                    timeout=15,
                )
            except Exception as e:
                log(f"查询预约列表出错: {e}")
                break

            data = safe_json_dict(r, "查询预约列表")
            if not data:
                break
            content = data.get("content") or {}
            items = content.get("defaultItems") or []
            if not isinstance(items, list):
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                booking_id = normalize_text(item.get("id"))
                status = normalize_text(item.get("status"))
                begin_ts = item.get("time")
                duration = item.get("duration")
                try:
                    end_ts = int(begin_ts) + int(duration)
                except (TypeError, ValueError):
                    end_ts = None
                row = {
                    "booking_id": booking_id,
                    "room_name": normalize_text(item.get("roomName")),
                    "seat_num": normalize_seat_num(item.get("seatNum")),
                    "begin_ts": int(begin_ts) if normalize_text(begin_ts).isdigit() else None,
                    "duration": int(duration) if normalize_text(duration).isdigit() else None,
                    "end_ts": end_ts,
                    "begin_text": format_cst_ts(begin_ts),
                    "end_text": format_cst_ts(end_ts, "%H:%M") if end_ts else "",
                    "status": status,
                    "status_text": BOOKING_STATUS_TEXT.get(status, f"状态{status}" if status else "未知"),
                    "can_cancel": status in ("0", "1"),
                    "raw": item,
                }
                if not only_active or row["can_cancel"]:
                    bookings.append(row)

            next_url = normalize_text(content.get("defaultNextUrl"))

        return bookings

    def run(self, dry_run=False):
        begin_ts = self.calc_begin_ts()
        dur_sec = self.calc_duration()
        seats = self.get_candidate_seats()
        settings = self.cfg.get("settings", {})
        max_r = int(settings.get("max_retries", 30))
        interval = float(settings.get("retry_interval", 1))

        t0 = datetime.datetime.fromtimestamp(begin_ts, CST).strftime("%Y-%m-%d %H:%M")
        t1 = datetime.datetime.fromtimestamp(begin_ts + dur_sec, CST).strftime("%H:%M")
        log(f"目标时段：{t0} ~ {t1}，候选座位 {len(seats)} 个")

        if dry_run:
            for target in seats:
                seat = self.resolve_seat(target, begin_ts, dur_sec)
                if seat:
                    log(f"可解析：{seat['room_name']} {seat['seat_num']} -> seat_id={seat['seat_id']}")
                else:
                    log(f"未找到：{target['room_name']} {target['seat_num']}")
            return True

        for attempt in range(1, max_r + 1):
            log(f"第 {attempt}/{max_r} 轮")
            for target in seats:
                log(f"尝试：{target['room_name']} {target['seat_num']}")
                if self.book_seat(target, begin_ts, dur_sec):
                    return True
            if attempt < max_r:
                time.sleep(interval)

        log("全部重试结束，未能预约成功")
        return False

    def wait_until(self):
        h, m = map(int, self.cfg.get("settings", {}).get("trigger_time", "20:00").split(":"))
        now = now_cst()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)
        secs = (target - now).total_seconds()
        log(f"等待至 {target.strftime('%Y-%m-%d %H:%M:%S')}，还有 {int(secs // 60)} 分钟")
        while True:
            left = (target - now_cst()).total_seconds()
            if left <= 0:
                break
            time.sleep(min(30, max(0, left - 0.1)))
        log("开始抢座")


def build_parser():
    ap = argparse.ArgumentParser(description="杭电图书馆自习室抢座")
    ap.add_argument("--config", default="config.yaml", help="YAML 基础配置")
    ap.add_argument("--booking-json", default="booking.json", help="抢座 JSON 配置")
    ap.add_argument("--now", action="store_true", help="立即抢，不等待 trigger_time")
    ap.add_argument("--dry-run", action="store_true", help="只解析 JSON 里的座位，不提交预约")
    ap.add_argument("--list-bookings", action="store_true", help="列出我的座位预约")
    ap.add_argument("--cancel-booking", help="取消指定 bookingId")
    ap.add_argument("--show-browser", action="store_true", help="显示 Chrome 登录窗口")
    ap.add_argument("--cookie-file", default="cookies.json", help="cookie 缓存文件")
    ap.add_argument("--refresh-cookie", action="store_true", help="忽略已保存 cookie，强制重新登录")
    return ap


def main():
    args = build_parser().parse_args()
    booker = LibraryBooker(
        cfg_path=args.config,
        booking_path=args.booking_json,
        headless=not args.show_browser,
        cookie_file=args.cookie_file,
        refresh_cookie=args.refresh_cookie,
    )

    if not booker.login():
        sys.exit(1)

    if args.list_bookings:
        bookings = booker.list_bookings(pages=1)
        if not bookings:
            log("暂无座位预约")
            sys.exit(0)
        for item in bookings:
            cancel_mark = "可取消" if item["can_cancel"] else "不可取消"
            log(
                f"{item['booking_id']} | {item['room_name']} {item['seat_num']} | "
                f"{item['begin_text']} ~ {item['end_text']} | {item['status_text']} | {cancel_mark}"
            )
        sys.exit(0)

    if args.cancel_booking:
        ok, _ = booker.cancel_booking(args.cancel_booking)
        sys.exit(0 if ok else 1)

    if not args.now and not args.dry_run:
        booker.wait_until()
    ok = booker.run(dry_run=args.dry_run)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
