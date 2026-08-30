#!/usr/bin/env python3
"""
Poll the OmniFlash server until it responds before opening the browser.
Exits 0 as soon as the server responds on port 8086, or 1 if it times out.
"""
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8086/api/heartbeat"
MAX_WAIT_SECONDS = 30
POLL_INTERVAL_SECONDS = 0.5


def main():
    deadline = time.time() + MAX_WAIT_SECONDS
    while time.time() < deadline:
        try:
            urllib.request.urlopen(URL, timeout=1)
            return 0
        except Exception:
            time.sleep(POLL_INTERVAL_SECONDS)
    return 1


if __name__ == "__main__":
    sys.exit(main())
