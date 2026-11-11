import unittest
from urbanwind_harness.resources import busy_cpus_from_samples, choose_slot, choose_shared_slot


class CpuLoadGateTests(unittest.TestCase):
    def test_busy_sibling_excludes_whole_core(self):
        groups = {(n, n + 128): n + 128 for n in range(32)}
        before = {n: [0] * 8 for n in range(256)}
        after = {n: [0, 0, 0, 100, 0, 0, 0, 0] for n in range(256)}
        for n in range(16): after[n] = [100, 0, 0, 0, 0, 0, 0, 0]
        busy, _ = busy_cpus_from_samples(before, after)
        self.assertEqual(choose_slot(groups, set(), busy), list(range(144, 160)))

    def test_no_idle_physical_cores_returns_no_slot(self):
        groups = {(n, n + 128): n + 128 for n in range(128)}
        self.assertIsNone(choose_slot(groups, set(), set(range(128))))

    def test_missing_and_nonadvancing_counters_fail_closed(self):
        busy, usage = busy_cpus_from_samples({0: [1] * 8, 1: [1] * 8}, {0: [1] * 8})
        self.assertEqual(busy, {0, 1})
        self.assertEqual(usage, {})

    def test_shared_mode_uses_idle_sibling_without_waiting_for_idle_core(self):
        groups = {(n, n + 128): n + 128 for n in range(32)}
        usage = {n: 100 for n in range(32)}
        usage.update({n + 128: 0 for n in range(32)})
        self.assertEqual(choose_shared_slot(groups, set(), set(), usage), list(range(128, 144)))

    def test_shared_mode_keeps_other_tasks_and_solvers_on_distinct_cores(self):
        groups = {(n, n + 128): n + 128 for n in range(32)}
        usage = {n: 0 for n in range(256)}
        slot = choose_shared_slot(groups, {128}, {129}, usage)
        self.assertEqual(slot, list(range(2, 18)))
        self.assertIsNone(choose_shared_slot(groups, set(range(17)), set(), usage))
