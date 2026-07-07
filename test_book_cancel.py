# -*- coding: utf-8 -*-
"""
One-shot test: wait, book one configured seat, then immediately cancel it.

Default behavior:
  1. wait 20 seconds
  2. use booking.json to book the first available candidate
  3. if booking succeeds, cancel it by bookingId
"""
import argparse
import datetime
import json
import sys
import time

from book import CST, LibraryBooker, log


def countdown(seconds):
    if seconds <= 0:
        return
    log(f"测试将在 {seconds} 秒后开始预约流程")
    end = time.time() + seconds
    while True:
        left = int(round(end - time.time()))
        if left <= 0:
            break
        if left <= 10 or left % 10 == 0:
            log(f"还剩 {left} 秒")
        time.sleep(min(1, max(0, left)))


def save_response(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def response_message(result):
    response = result.get("response") or {}
    data = response.get("DATA")
    if isinstance(data, dict) and data.get("msg"):
        return str(data.get("msg"))
    return str(response.get("MESSAGE") or "")


def find_created_booking(booker, result, begin_ts, dur_sec):
    seat = result.get("seat") or {}
    target_room = seat.get("room_name")
    target_num = str(seat.get("seat_num") or "")
    target_end = begin_ts + dur_sec
    for booking in booker.list_bookings(pages=1, only_active=True):
        if booking.get("begin_ts") != begin_ts:
            continue
        if booking.get("end_ts") != target_end:
            continue
        if booking.get("room_name") != target_room:
            continue
        if str(booking.get("seat_num") or "") != target_num:
            continue
        return booking
    return None


def build_parser():
    ap = argparse.ArgumentParser(description="预约后立即取消的测试脚本")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--booking-json", default="booking.json")
    ap.add_argument("--cookie-file", default="cookies.json")
    ap.add_argument("--wait-seconds", type=int, default=20, help="启动后等待多少秒再预约")
    ap.add_argument("--show-browser", action="store_true", help="显示浏览器用于手动登录")
    ap.add_argument("--refresh-cookie", action="store_true", help="强制重新登录")
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
        return 1

    begin_ts = booker.calc_begin_ts()
    dur_sec = booker.calc_duration()
    begin = datetime.datetime.fromtimestamp(begin_ts, CST).strftime("%Y-%m-%d %H:%M")
    end = datetime.datetime.fromtimestamp(begin_ts + dur_sec, CST).strftime("%H:%M")
    candidates = booker.get_candidate_seats()
    log(f"测试目标时段：{begin} ~ {end}，候选座位 {len(candidates)} 个")

    countdown(args.wait_seconds)
    log("开始测试预约")

    last_result = None
    for target in candidates:
        log(f"测试尝试：{target['room_name']} {target['seat_num']}")
        result = booker.book_seat_result(target, begin_ts, dur_sec)
        last_result = result
        if not result.get("ok"):
            if "已有预约" in response_message(result):
                save_response("last_booking_test.json", {"last_result": last_result})
                log("账号当前时段已有预约，测试停止；不会取消已有预约。")
                return 5
            continue

        booking_id = result.get("booking_id")
        if not booking_id:
            created = find_created_booking(booker, result, begin_ts, dur_sec)
            if created:
                booking_id = created.get("booking_id")
                log(f"预约返回里没有 bookingId，已从预约列表反查到 bookingId={booking_id}")
            else:
                save_response("last_booking_response.json", result.get("response"))
                log("预约成功，但返回和预约列表里都没有找到 bookingId，已保存 last_booking_response.json")
                log("为避免误操作，脚本没有继续猜测取消参数。请手动检查网页预约记录。")
                return 2

        log(f"拿到 bookingId={booking_id}，开始取消")
        ok, cancel_response = booker.cancel_booking(booking_id)
        save_response(
            "last_booking_test.json",
            {
                "booking_result": result.get("response"),
                "booking_id": booking_id,
                "cancel_result": cancel_response,
                "cancel_ok": ok,
            },
        )
        return 0 if ok else 3

    save_response("last_booking_test.json", {"last_result": last_result})
    log("所有候选座位都没有预约成功，所以无需取消")
    return 4


if __name__ == "__main__":
    sys.exit(main())
