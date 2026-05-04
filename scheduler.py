#!/usr/bin/env python3
"""
Fallback scheduler — runs eth_vol_brief.py daily at 23:00 SGT (15:00 UTC).
Start with: nohup python3 /home/user/scvol/scheduler.py &
"""
import time, subprocess, datetime, os, sys

SCRIPT   = "/home/user/scvol/eth_vol_brief.py"
PYTHON   = "/usr/local/bin/python3"
LOG_DIR  = "/home/user/scvol/reports"
RUN_HOUR_UTC = 15  # 23:00 SGT
RUN_MIN_UTC  = 0

def already_ran_today(today):
    log = os.path.join(LOG_DIR, f"cron_{today.strftime('%Y%m%d')}.log")
    return os.path.exists(log)

print(f"[scheduler] started — will trigger daily at {RUN_HOUR_UTC:02d}:{RUN_MIN_UTC:02d} UTC", flush=True)
last_run = None

while True:
    now = datetime.datetime.now(datetime.timezone.utc)
    if (now.hour == RUN_HOUR_UTC and now.minute == RUN_MIN_UTC
            and last_run != now.date()):
        print(f"[scheduler] firing at {now.isoformat()}", flush=True)
        log_path = os.path.join(LOG_DIR, f"cron_{now.strftime('%Y%m%d')}.log")
        with open(log_path, "a") as lf:
            subprocess.run([PYTHON, SCRIPT], stdout=lf, stderr=lf)
        last_run = now.date()
        print(f"[scheduler] done — log: {log_path}", flush=True)
    time.sleep(30)
