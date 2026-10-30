import threading
import time
import unittest

from tournament_control.operations import LatestTaskRunner


class LatestTaskRunnerTests(unittest.TestCase):
    def test_running_task_keeps_only_latest_pending_request(self):
        runner = LatestTaskRunner("test-latest-task")
        started = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        results = []

        def first():
            started.set()
            release.wait(1.0)
            return 1

        runner.submit(first, results.append, self.fail)
        self.assertTrue(started.wait(1.0))
        runner.submit(lambda: 2, results.append, self.fail)
        runner.submit(lambda: 3, lambda value: (results.append(value), completed.set()), self.fail)
        release.set()

        self.assertTrue(completed.wait(1.0))
        self.assertEqual(results, [1, 3])
        runner.close()

    def test_close_discards_pending_callback(self):
        runner = LatestTaskRunner("test-closed-task")
        started = threading.Event()
        release = threading.Event()
        results = []

        def blocked():
            started.set()
            release.wait(1.0)
            return "done"

        runner.submit(blocked, results.append, self.fail)
        self.assertTrue(started.wait(1.0))
        runner.close()
        release.set()
        time.sleep(0.03)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
