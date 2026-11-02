"""Supervise the scheduler, never replay model executions. POSIX only."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

HERE=Path(__file__).resolve().parent


def alive(pid, root):
    if not pid:
        return False
    result=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True)
    return str(HERE/'watchdog.py') in result.stdout and str(root) in result.stdout


def ensure_watchdog(root):
    import fcntl
    from worker import save
    with open(root/'watchdog-launch.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:
            current=json.loads((root/'watchdog.json').read_text())
        except (OSError,ValueError):
            current={}
        if alive(current.get('pid'),root):
            return
        child=subprocess.Popen([sys.executable,str(HERE/'watchdog.py'),'--root',str(root)],
                               stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                               start_new_session=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        save(root/'watchdog.json',{'pid':child.pid})
        threading.Thread(target=child.wait,daemon=True).start()


def supervise(root):
    import fcntl
    from scheduler import Scheduler, process_alive
    with open(root/'watchdog.lock','a') as lease:
        try:
            fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return
        scheduler=Scheduler(root)
        while True:
            with scheduler.db() as db:
                pending=db.execute("SELECT 1 FROM tasks WHERE status IN ('running','cancelling','queued') LIMIT 1").fetchone()
                pid=db.execute("SELECT value FROM settings WHERE key='daemon_pid'").fetchone()[0]
            if not pending:
                return
            running=process_alive(pid,root)
            try:
                heartbeat=json.loads((root/'scheduler-heartbeat.json').read_text())
            except (ValueError,OSError):
                heartbeat={}
            if running and heartbeat.get('pid')==pid and time.time()-heartbeat['at']>45:
                # Check exact command/root before each signal; only the supervisor dies.
                os.kill(pid,signal.SIGTERM)
                end=time.monotonic()+2
                while process_alive(pid,root) and time.monotonic()<end:
                    time.sleep(.1)
                if process_alive(pid,root):
                    os.kill(pid,signal.SIGKILL)
                running=process_alive(pid,root)
            if not running:
                scheduler.kick()
            time.sleep(.3)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    supervise(parser.parse_args().root)
