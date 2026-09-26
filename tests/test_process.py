import sys
import threading
import time
import unittest

from caid_chat.process import CancelToken, CancelledError, run_command


class ProcessTests(unittest.TestCase):
    def test_cancel_stops_subprocess(self):
        token = CancelToken()
        outcomes = []

        def run():
            try:
                run_command([sys.executable, "-c", "import time; time.sleep(10)"], timeout=15, token=token)
            except CancelledError:
                outcomes.append("cancelled")

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.1)
        token.cancel()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes, ["cancelled"])

    def test_cancel_does_not_wait_for_child_inheriting_output_pipes(self):
        token = CancelToken()
        outcomes = []
        script = ("import subprocess,sys,time; "
                  "subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); "
                  "time.sleep(10)")

        def run():
            try:
                run_command([sys.executable, "-c", script], timeout=15, token=token)
            except CancelledError:
                outcomes.append("cancelled")

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.25)
        token.cancel()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes, ["cancelled"])


if __name__ == "__main__":
    unittest.main()
