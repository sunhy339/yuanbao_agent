
import sys
import time

sys.stdout.write("out-1")
sys.stdout.flush()
sys.stderr.write("err-1")
sys.stderr.flush()
time.sleep(0.5)
sys.stdout.write("out-2\n")
sys.stdout.flush()
sys.stderr.write("err-2\n")
sys.stderr.flush()
time.sleep(0.1)
