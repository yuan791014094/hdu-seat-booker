# -*- coding: utf-8 -*-
"""
查询所有自习室座位ID，帮你填写 config.yaml
运行：python query_seats.py
"""
import io, sys, time, datetime, yaml, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

BASE = "https://hdu.huitu.zhishulib.com"


def selenium_login(username, password):
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    driver = webdriver.Chrome(options=opts)
    wait = WebDriverWait(driver, 30)
    try:
        target = f"{BASE}/User/Index/hduCASLogin?forward=%2FSpace%2FCategory%2Flist"
        driver.get(f"https://sso.hdu.edu.cn/login?service={requests.utils.quote(target, safe='')}")
        wait.until(lambda d: any(i.get_attribute("type") == "password"
                                  for i in d.find_elements(By.TAG_NAME, "input")))
        time.sleep(0.3)
        inputs = driver.find_elements(By.TAG_NAME, "input")
        inputs[0].send_keys(username)
        inputs[1].send_keys(password)
        driver.find_element(By.CSS_SELECTOR, "button[type=submit]").click()
        wait.until(EC.url_contains("hdu.huitu.zhishulib.com"))
        time.sleep(1)
        return {c["name"]: c["value"] for c in driver.get_cookies()}
    finally:
        driver.quit()


def main():
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    print("登录中...")
    cookies = selenium_login(cfg["account"]["username"], cfg["account"]["password"])

    sess = requests.Session()
    sess.cookies.update(cookies)
    sess.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    s = cfg["settings"]
    tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
    begin_ts = int(tomorrow.replace(hour=8, minute=0, second=0, microsecond=0).timestamp())

    payload = {
        "space_category[category_id]": s["category_id"],
        "space_category[content_id]": s["content_id"],
        "beginTime": begin_ts,
        "duration": 3600,
        "num": 1,
    }
    r = sess.post(f"{BASE}/Seat/Index/searchSeats?LAB_JSON=1", data=payload, timeout=15)
    data = r.json()

    for block in data.get("content", {}).get("children", []):
        item = block.get("children", {})
        pois = item.get("seatMap", {}).get("POIs", [])
        if not pois:
            continue
        room_name = item.get("roomName", "未知")
        category_id = pois[0].get("category_id", "")
        available = sum(1 for p in pois if p["state"] == 0)
        print(f"\n【{room_name}】category_id={category_id}  可用{available}/{len(pois)}")
        print(f"  {'座位号':<8} {'seat_id':<10} 状态")
        for p in sorted(pois, key=lambda x: x["title"]):
            state_str = "✅可用" if p["state"] == 0 else "❌占用"
            print(f"  {p['title']:<8} {p['id']:<10} {state_str}")

    print("\n把你想要的座位的 seat_id 和 category_id 填入 config.yaml 的 seats_priority 里。")


if __name__ == "__main__":
    main()
