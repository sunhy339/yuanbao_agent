
import json
import sys
import time

for raw_line in sys.stdin:
    json.loads(raw_line)
    sys.stdout.write("partial-stdout")
    sys.stdout.flush()
    sys.stderr.write("partial-stderr")
    sys.stderr.flush()
    time.sleep(0.5)
