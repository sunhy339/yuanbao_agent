
import json
import sys
import time

for raw_line in sys.stdin:
    request = json.loads(raw_line)
    sys.stdout.write("log:")
    sys.stdout.flush()
    time.sleep(0.05)
    sys.stdout.write("ready\n")
    sys.stdout.flush()
    sys.stderr.write("warn:")
    sys.stderr.flush()
    time.sleep(0.05)
    sys.stderr.write("done\n")
    sys.stderr.flush()
    sys.stdout.write(json.dumps({
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {"ok": True},
    }) + "\n")
    sys.stdout.flush()
