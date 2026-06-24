# -*- coding: utf-8 -*-
"""
杭电图书馆自动抢座脚本
登录方式：Selenium（处理CAS SSO）
预约方式：requests（Cookie复用）
"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import time, datetime, hashlib, base64, argparse, yaml, requests, os

# GitHub Actions 服务器在 UTC，统一用 UTC+8
def now_cst():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)

# ── Selenium（仅登录用）──────────────────────────────────────────────────────
def selenium_login(username, password):
    """用 Selenium 完成 CAS 登录，返回 cookie 字典"""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")

    driver = webdriver.Chrome(options=opts)
    wait = WebDriverWait(driver, 20)

    try:
        target = "https://hdu.huitu.zhishulib.com/User/Index/hduCASLogin?forward=%2FSpace%2FCategory%2Flist"
        cas_url = f"https://sso.hdu.edu.cn/login?service={requests.utils.quote(target, safe='')}"
        driver.get(cas_url)

        # 等待password框出现（Angular SSO页面需要JS渲染）
        wait.until(lambda d: any(
            i.get_attribute("type") == "password"
            for i in d.find_elements(By.TAG_NAME, "input")
        ))
        time.sleep(0.3)
        inputs = driver.find_elements(By.TAG_NAME, "input")
        un_box = inputs[0]  # 第一个 text
        pw_box = inputs[1]  # 第二个 password

        un_box.clear(); un_box.send_keys(username)
        pw_box.clear(); pw_box.send_keys(password)

        btn = driver.find_element(By.CSS_SELECTOR, "button[type=submit]")
        btn.click()

        # 等待跳转回图书馆
        wait.until(EC.url_contains("hdu.huitu.zhishulib.com"))
        time.sleep(1)

        cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
        print(f"✅ 登录成功，获取到 {len(cookies)} 个 Cookie")
        return cookies
    finally:
        driver.quit()


# ── 核心逻辑 ─────────────────────────────────────────────────────────────────
BASE = "https://hdu.huitu.zhishulib.com"


def log(msg):
    print(f"[{now_cst().strftime('%H:%M:%S')} CST] {msg}")


class LibraryBooker:
    def __init__(self, cfg_path="config.yaml"):
        with open(cfg_path, encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
        # 支持从环境变量覆盖账号密码（GitHub Actions Secrets）
        if os.environ.get("HDU_USERNAME"):
            self.cfg["account"]["username"] = os.environ["HDU_USERNAME"]
        if os.environ.get("HDU_PASSWORD"):
            self.cfg["account"]["password"] = os.environ["HDU_PASSWORD"]
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        self.uid = None

    def login(self):
        acc = self.cfg["account"]
        cookies = selenium_login(acc["username"], acc["password"])
        self.session.cookies.update(cookies)

        # 验证登录并拿uid（从searchSeats接口）
        r = self.session.get(
            f"{BASE}/Seat/Index/searchSeats?LAB_JSON=1"
            "&space_category[category_id]=591&space_category[content_id]=3",
            timeout=10
        )
        data = r.json().get("data", {})
        if data.get("is_login"):
            self.uid = data["uid"]
            log(f"uid = {self.uid}")
            return True
        log("❌ 登录验证失败")
        return False

    def calc_begin_ts(self):
        b = self.cfg["booking"]
        now = now_cst()
        base_date = now.date() if b["date"] == "today" else (now + datetime.timedelta(days=1)).date()
        # 构造 CST 时间，再转为 UTC 时间戳
        dt_cst = datetime.datetime(base_date.year, base_date.month, base_date.day, b["begin_hour"])
        dt_utc = dt_cst - datetime.timedelta(hours=8)
        return int(dt_utc.timestamp())

    def query_seat(self, seat_id, begin_ts, dur_sec):
        s = self.cfg["settings"]
        payload = {
            "space_category[category_id]": s["category_id"],
            "space_category[content_id]": s["content_id"],
            "beginTime": begin_ts,
            "duration": dur_sec,
            "num": 1,
        }
        try:
            data = self.session.post(f"{BASE}/Seat/Index/searchSeats?LAB_JSON=1",
                                     data=payload, timeout=10).json()
            for blk in data.get("content", {}).get("children", []):
                for p in blk.get("children", {}).get("seatMap", {}).get("POIs", []):
                    if str(p["id"]) == str(seat_id):
                        return "available" if p["state"] == 0 else "occupied"
        except Exception as e:
            log(f"  查询出错: {e}")
        return "unknown"

    def book_seat(self, seat, begin_ts, dur_sec):
        api_time = int(time.time())
        seat_id = seat["seat_id"]

        # 签名（知书平台要求）
        raw = (f"post&/Seat/Index/bookSeats?LAB_JSON=1"
               f"&api_time{api_time}&beginTime{begin_ts}"
               f"&duration{dur_sec}&is_recommend0"
               f"&seatBookers[0]{self.uid}&seats[0]{seat_id}")
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
            d = r.json()
            code = d.get("CODE", "")
            msg = d.get("MESSAGE") or d.get("DATA", {}).get("msg", "")
            if code == "ok":
                log(f"🎉 预约成功！{seat['room_name']} {seat['seat_num']} | {msg}")
                return True
            log(f"  [{code}] {msg}")
        except Exception as e:
            log(f"  请求出错: {e}")
        return False

    def run(self):
        cfg = self.cfg
        begin_ts = self.calc_begin_ts()
        dur_sec = cfg["booking"]["duration_hours"] * 3600
        seats = cfg["seats_priority"]
        max_r = cfg["settings"]["max_retries"]
        interval = cfg["settings"]["retry_interval"]

        t0 = datetime.datetime.fromtimestamp(begin_ts).strftime("%Y-%m-%d %H:%M")
        t1 = datetime.datetime.fromtimestamp(begin_ts + dur_sec).strftime("%H:%M")
        log(f"目标时段：{t0} ~ {t1}，候选座位 {len(seats)} 个")

        for attempt in range(1, max_r + 1):
            log(f"── 第 {attempt}/{max_r} 轮 ──")
            for seat in seats:
                log(f"  → {seat['room_name']} {seat['seat_num']} (id={seat['seat_id']})")
                if self.book_seat(seat, begin_ts, dur_sec):
                    return True
            if attempt < max_r:
                time.sleep(interval)

        log("❌ 全部重试结束，未能预约成功")
        return False

    def wait_until(self):
        h, m = map(int, self.cfg["settings"]["trigger_time"].split(":"))
        now = datetime.datetime.now()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)
        secs = (target - now).total_seconds()
        log(f"⏰ 等待至 {target.strftime('%Y-%m-%d %H:%M:%S')}（还有 {int(secs//60)} 分钟）")
        while True:
            left = (target - datetime.datetime.now()).total_seconds()
            if left <= 0:
                break
            time.sleep(min(30, max(0, left - 0.1)))
        log("⚡ 开始抢座！")


# ── 入口 ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--now", action="store_true", help="立即抢，不等定时")
    ap.add_argument("--query", action="store_true", help="只查询座位状态")
    args = ap.parse_args()

    booker = LibraryBooker(args.config)

    if not booker.login():
        sys.exit(1)

    if args.query:
        begin_ts = booker.calc_begin_ts()
        dur_sec = booker.cfg["booking"]["duration_hours"] * 3600
        for seat in booker.cfg["seats_priority"]:
            s = booker.query_seat(seat["seat_id"], begin_ts, dur_sec)
            log(f"  {'✅' if s=='available' else '❌'} {seat['room_name']} {seat['seat_num']}: {s}")
    else:
        if not args.now:
            booker.wait_until()
        booker.run()
