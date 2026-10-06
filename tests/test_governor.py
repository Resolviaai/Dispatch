"""Unit test for Phase 4: Resource Governor & Laptop Performance Manager."""
import unittest
from unittest.mock import patch, MagicMock
from dispatch.governor.resource_governor import ResourceGovernor, GovernorPolicy


class TestResourceGovernor(unittest.TestCase):
    def test_live_metrics_retrieval(self):
        governor = ResourceGovernor()
        metrics = governor.get_system_metrics()
        self.assertIn("free_disk_gb", metrics)
        self.assertIn("on_ac_power", metrics)
        self.assertIn("cpu_percent", metrics)
        self.assertIn("idle_seconds", metrics)
        self.assertGreater(metrics["total_disk_gb"], 0)

    def test_optimal_worker_threads(self):
        policy = GovernorPolicy(reserved_cpu_cores=2)
        governor = ResourceGovernor(policy=policy)
        threads = governor.get_optimal_worker_threads()
        self.assertGreaterEqual(threads, 1)

    def test_low_process_priority(self):
        governor = ResourceGovernor()
        # Should succeed or gracefully handle without throwing exceptions
        success = governor.set_low_process_priority()
        self.assertIsInstance(success, bool)

    def test_disk_space_policy_enforcement(self):
        # Policy requiring 500 GB free disk space (which test machine doesn't have)
        policy = GovernorPolicy(min_free_disk_gb=500.0)
        governor = ResourceGovernor(policy=policy)
        can_run, reason = governor.can_process_heavy_task()
        self.assertFalse(can_run)
        self.assertIn("Disk space low", reason)

    def test_battery_policy_enforcement(self):
        # Mock battery status: on battery, 20%
        mock_battery = MagicMock()
        mock_battery.power_plugged = False
        mock_battery.percent = 20.0

        with patch("psutil.sensors_battery", return_value=mock_battery):
            # Case 1: allow_on_battery = False -> Should disallow
            policy1 = GovernorPolicy(allow_on_battery=False, min_free_disk_gb=1.0)
            gov1 = ResourceGovernor(policy1)
            can_run1, reason1 = gov1.can_process_heavy_task()
            self.assertFalse(can_run1)
            self.assertIn("battery", reason1.lower())

            # Case 2: allow_on_battery = True, but min_battery_percent = 30% -> Should disallow
            policy2 = GovernorPolicy(allow_on_battery=True, min_battery_percent=30.0, min_free_disk_gb=1.0)
            gov2 = ResourceGovernor(policy2)
            can_run2, reason2 = gov2.can_process_heavy_task()
            self.assertFalse(can_run2)
            self.assertIn("too low", reason2.lower())

            # Case 3: allow_on_battery = True, min_battery_percent = 15% -> Should allow
            policy3 = GovernorPolicy(allow_on_battery=True, min_battery_percent=15.0, min_free_disk_gb=1.0)
            gov3 = ResourceGovernor(policy3)
            with patch.object(gov3, "get_user_idle_seconds", return_value=200.0):
                can_run3, reason3 = gov3.can_process_heavy_task()
                self.assertTrue(can_run3)

    def test_user_activity_policy_enforcement(self):
        policy = GovernorPolicy(pause_when_user_active=True, idle_threshold_seconds=60.0, min_free_disk_gb=1.0)
        governor = ResourceGovernor(policy=policy)

        # Mock user actively typing (idle 5 seconds)
        with patch.object(governor, "get_user_idle_seconds", return_value=5.0):
            mock_battery = MagicMock()
            mock_battery.power_plugged = True
            mock_battery.percent = 100.0
            with patch("psutil.sensors_battery", return_value=mock_battery):
                can_run, reason = governor.can_process_heavy_task()
                self.assertFalse(can_run)
                self.assertIn("actively using", reason)


if __name__ == "__main__":
    unittest.main()
