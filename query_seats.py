# -*- coding: utf-8 -*-
"""
Export HDU library study-room seat mappings to Markdown.

The query is only for study rooms. It does not judge whether a seat is currently
free; the purpose is to map visible seat numbers to internal seat_id values.
"""
import argparse
import datetime
import io
import os
import sys

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
    cst_timestamp,
    normalize_seat_num,
    normalize_text,
    now_cst,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_FILE = os.path.join(SCRIPT_DIR, "自习室座位清单.md")


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
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期格式应为 auto、today、tomorrow、day_after_tomorrow 或 YYYY-MM-DD") from exc


def abs_path(path):
    return path if os.path.isabs(path) else os.path.join(SCRIPT_DIR, path)


def compact_ranges(values):
    nums = sorted({int(v) for v in values if str(v).isdigit()})
    ranges = []
    start = prev = None
    for num in nums:
        if start is None:
            start = prev = num
            continue
        if num == prev + 1:
            prev = num
            continue
        ranges.append((start, prev))
        start = prev = num
    if start is not None:
        ranges.append((start, prev))
    parts = [str(a) if a == b else f"{a}-{b}" for a, b in ranges]
    return "、".join(parts) if parts else "无连续数字座位"


def markdown_table(rows):
    lines = ["| 显示座位号 | 实际 seat_id |", "|---:|---:|"]
    for seat in rows:
        lines.append(f"| {seat['seat_num']} | {seat['seat_id']} |")
    return "\n".join(lines)


def build_markdown(grouped, begin_ts, dur_sec, room_filter=None, seat_filter=None):
    begin_dt = datetime.datetime.fromtimestamp(begin_ts, CST)
    end_dt = datetime.datetime.fromtimestamp(begin_ts + dur_sec, CST)
    lines = [
        "# 自习室座位清单",
        "",
        f"- 查询时间段：{begin_dt.strftime('%Y-%m-%d %H:%M')} ~ {end_dt.strftime('%H:%M')}",
        "- 用途：只查看显示座位号和实际 seat_id 的映射，不判断当前是否空闲。",
        "- 抢座时只需要把房间名和显示座位号写入 `booking.json`。",
        "",
        "## 自习室内部房间",
        "",
    ]

    total = 0
    for room in STUDY_ROOM_NAMES:
        seats = grouped.get(room, [])
        if room_filter and room_filter not in room:
            continue
        if seat_filter:
            seats = [s for s in seats if normalize_seat_num(s["seat_num"]) == seat_filter]
        if not seats:
            continue
        total += len(seats)
        nums = [s["seat_num"] for s in seats]
        lines.extend(
            [
                f"### {room}",
                "",
                f"- 座位数量：{len(seats)}",
                f"- 显示座位号范围：{compact_ranges(nums)}",
                "",
                markdown_table(seats),
                "",
            ]
        )

    lines.insert(5, f"- 本次输出座位数：{total}")
    if total == 0:
        lines.append("没有找到匹配的房间或座位。")
    return "\n".join(lines).rstrip() + "\n"


def resolve_query_time(args, booker):
    booking = booker.booking_config()
    day = args.date or parse_date_token(booking.get("date", "auto"))
    begin_hour = args.begin_hour
    if begin_hour is None:
        begin_hour = int(booking.get("begin_hour", 8))
    if args.duration_hours is not None:
        duration_hours = int(args.duration_hours)
    else:
        cfg_duration = booking.get("duration_hours")
        duration_hours = int(cfg_duration) if cfg_duration not in (None, "") else max(1, 22 - begin_hour)
    begin_ts = cst_timestamp(day, begin_hour, 0)
    return begin_ts, duration_hours * 3600


def build_parser():
    ap = argparse.ArgumentParser(description="导出自习室座位号和 seat_id 映射")
    ap.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="YAML 基础配置")
    ap.add_argument("--booking-json", default=DEFAULT_BOOKING_FILE, help="抢座 JSON 配置")
    ap.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE, help="cookie 缓存文件")
    ap.add_argument("--refresh-cookie", action="store_true", help="忽略已保存 cookie，强制重新登录")
    ap.add_argument("--show-browser", action="store_true", help="显示 Chrome 登录窗口")
    ap.add_argument("--date", type=parse_date_token, help="查询日期：today、tomorrow 或 YYYY-MM-DD")
    ap.add_argument("--begin-hour", type=int, help="开始小时，0-23")
    ap.add_argument("--duration-hours", type=int, help="持续小时数")
    ap.add_argument("--room-name", default=DEFAULT_STUDY_ROOM_NAME, help="只输出房间名包含该文字的自习室")
    ap.add_argument("--seat-num", help="只输出指定显示座位号")
    ap.add_argument("--output", default=DEFAULT_OUTPUT_FILE, help="Markdown 输出文件")
    ap.add_argument("--print", action="store_true", help="同时把 Markdown 打印到终端")
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
        print("登录失败：请先用 python book.py --show-browser --dry-run 手动登录一次。")
        return 1

    begin_ts, dur_sec = resolve_query_time(args, booker)
    grouped = booker.group_study_room_seats(begin_ts, dur_sec)
    room_filter = normalize_text(args.room_name)
    if room_filter and not grouped.get(room_filter):
        history_room = booker.get_room_map_from_booking_history(room_filter)
        if history_room:
            grouped[history_room["room_name"]] = history_room["seats"]
    seat_filter = normalize_seat_num(args.seat_num)
    markdown = build_markdown(grouped, begin_ts, dur_sec, room_filter, seat_filter)

    output = abs_path(args.output)
    with open(output, "w", encoding="utf-8", newline="\n") as f:
        f.write(markdown)
    print(f"已生成：{output}")

    if args.print:
        print()
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
