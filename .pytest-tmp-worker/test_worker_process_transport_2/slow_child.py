
import json
import sys
import time

for raw_line in sys.stdin:
    request = json.loads(raw_line)
    time.sleep(0.5)
    sys.stdout.write(json.dumps({
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {"ok": True},
    }) + "\n")
    sys.stdout.flush()
