import threading
import unittest
from concurrent.futures import Future, ThreadPoolExecutor

from code_mortis.core.errors import ReviewStopped
from code_mortis.security_scans.service import rolling_analysis


class SecuritySchedulingTests(unittest.TestCase):
    def test_free_slots_refill_before_slowest_file_and_results_keep_inventory_order(self):
        paths = ["slow.py", "fast.py", "next.py", "last.py"]
        started = {path: threading.Event() for path in paths}
        release = {path: threading.Event() for path in paths}
        stop = threading.Event()
        lock = threading.Lock()
        active, peak, results, errors, submitted = 0, 0, {}, [], []

        def work(path):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            started[path].set()
            try:
                if not release[path].wait(5):
                    raise AssertionError("Test did not release its fake inference.")
                return {"findings": [path]}
            finally:
                with lock:
                    active -= 1

        with ThreadPoolExecutor(max_workers=2) as pool:
            def submit(path):
                submitted.append(path)
                return pool.submit(work, path)

            def collect():
                try:
                    for path, future in rolling_analysis(paths, 2, submit, lambda: False, stop):
                        results[path] = future.result()
                except BaseException as exc:
                    errors.append(exc)

            collector = threading.Thread(target=collect)
            collector.start()
            try:
                self.assertTrue(started["slow.py"].wait(5))
                self.assertTrue(started["fast.py"].wait(5))
                self.assertFalse(started["next.py"].is_set())
                self.assertEqual(submitted, paths[:2])
                release["fast.py"].set()
                self.assertTrue(started["next.py"].wait(5))
                self.assertFalse(release["slow.py"].is_set())
                self.assertEqual(submitted, paths[:3])
                release["next.py"].set()
                self.assertTrue(started["last.py"].wait(5))
            finally:
                for event in release.values():
                    event.set()
                collector.join(5)
            self.assertFalse(collector.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(peak, 2)
        self.assertEqual([finding for path in paths for finding in results[path]["findings"]], paths)

    def test_failure_or_unknown_usage_drains_started_work_without_refilling(self):
        for failure in [RuntimeError("provider failed"), ValueError("Token usage is unknown")]:
            with self.subTest(failure=failure):
                submitted, consumed = [], []
                stop = threading.Event()

                def submit(path, submitted=submitted, failure=failure):
                    submitted.append(path)
                    future = Future()
                    if path == "failed.py":
                        future.set_exception(failure)
                    else:
                        future.set_result(path)
                    return future

                for path, future in rolling_analysis(["failed.py", "started.py", "later.py"], 2, submit, lambda: False, stop):
                    try:
                        future.result()
                    except (RuntimeError, ValueError):
                        stop.set()
                    consumed.append(path)
                self.assertEqual(submitted, ["failed.py", "started.py"])
                self.assertCountEqual(consumed, submitted)

    def test_revocation_before_refill_stops_submission_and_cancels_pending_work(self):
        pending = Future()
        completed = Future()
        completed.set_result("done")
        submitted, revoked = [], threading.Event()
        stop = threading.Event()

        def submit(path):
            submitted.append(path)
            return pending if path == "pending.py" else completed

        results = rolling_analysis(["pending.py", "done.py", "later.py"], 2, submit, revoked.is_set, stop)
        self.assertEqual(next(results), ("done.py", completed))
        revoked.set()
        with self.assertRaises(ReviewStopped):
            next(results)
        self.assertEqual(submitted, ["pending.py", "done.py"])
        self.assertTrue(pending.cancelled())
        self.assertTrue(stop.is_set())

    def test_cancelled_scan_never_submits_and_submission_errors_stop_workers(self):
        for stopped, expected in [(lambda: True, ReviewStopped), (lambda: False, ValueError)]:
            with self.subTest(expected=expected):
                stop = threading.Event()
                submitted = []

                def submit(path, submitted=submitted):
                    submitted.append(path)
                    raise ValueError("Token usage is unknown")

                with self.assertRaises(expected):
                    list(rolling_analysis(["file.py"], 2, submit, stopped, stop))
                self.assertEqual(submitted, [] if expected is ReviewStopped else ["file.py"])
                self.assertTrue(stop.is_set())


if __name__ == "__main__":
    unittest.main()
