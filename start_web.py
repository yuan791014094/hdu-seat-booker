# -*- coding: utf-8 -*-
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
import webbrowser


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("HDU_BOOK_WEB_PORT", "8765"))
URL = f"http://127.0.0.1:{PORT}"
STDOUT_LOG = os.path.join(SCRIPT_DIR, "web_app_stdout.log")
STDERR_LOG = os.path.join(SCRIPT_DIR, "web_app_stderr.log")


def is_web_alive():
    try:
        with urllib.request.urlopen(URL, timeout=1.0) as resp:
            return 200 <= resp.status < 500
    except (OSError, urllib.error.URLError):
        return False


def start_web_app():
    stdout = open(STDOUT_LOG, "ab")
    stderr = open(STDERR_LOG, "ab")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creationflags |= subprocess.DETACHED_PROCESS

    subprocess.Popen(
        [sys.executable, os.path.join(SCRIPT_DIR, "web_app.py")],
        cwd=SCRIPT_DIR,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        close_fds=False,
        creationflags=creationflags,
    )


def wait_until_ready(seconds=12):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if is_web_alive():
            return True
        time.sleep(0.35)
    return False


def main():
    os.chdir(SCRIPT_DIR)
    if not is_web_alive():
        start_web_app()
        wait_until_ready()
    open_url = f"{URL}/?t={int(time.time())}"
    if not os.environ.get("HDU_BOOK_WEB_NO_OPEN"):
        webbrowser.open(open_url)
    print(open_url)


if __name__ == "__main__":
    main()
