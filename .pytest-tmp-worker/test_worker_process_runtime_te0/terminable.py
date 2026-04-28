
import pathlib
import signal
import sys
import time

marker = pathlib.Path(sys.argv[1])
ready = pathlib.Path(sys.argv[2])

def _handle(signum, _frame):
    marker.write_text(str(signum), encoding="utf-8")
    raise SystemExit(0)

if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, _handle)
if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, _handle)

ready.write_text("ready", encoding="utf-8")

while True:
    time.sleep(0.1)
