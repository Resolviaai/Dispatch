"""Resource Governor & Laptop Performance Manager.

Monitors system health, battery, CPU saturation, user idle/activity state,
and disk safety thresholds. Enforces opportunistic worker policies to prevent
laptop lag, battery depletion, or out-of-disk crashes.
"""
import os
import sys
import platform
import logging
from typing import Dict, Any, Tuple, Optional
from dataclasses import dataclass
import psutil

from dispatch.config import STORAGE_DIR

logger = logging.getLogger("dispatch.governor")


@dataclass
class GovernorPolicy:
    min_free_disk_gb: float = 5.0         # Stop new rendering/processing if disk free < 5 GB
    allow_on_battery: bool = False        # By default, avoid heavy rendering on battery
    min_battery_percent: float = 30.0     # If battery allowed, must be at least 30%
    max_cpu_percent: float = 88.0         # Pause/yield if background CPU exceeds 88%
    pause_when_user_active: bool = False  # If True, defer heavy jobs when user is typing/working
    idle_threshold_seconds: float = 90.0  # Seconds without keyboard/mouse to be considered idle
    reserved_cpu_cores: int = 2           # Keep 2 cores free for foreground OS and user apps


class ResourceGovernor:
    """Evaluates laptop resource conditions against policy before dispatching heavy pipeline tasks."""

    def __init__(self, policy: Optional[GovernorPolicy] = None):
        self.policy = policy or GovernorPolicy()
        self._is_windows = platform.system().lower() == "windows"

    def get_user_idle_seconds(self) -> float:
        """Returns the number of seconds since the last keyboard/mouse input on Windows."""
        if not self._is_windows:
            return 999.0  # Fallback for headless or non-Windows environments

        try:
            import ctypes
            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]

            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                millis_since_boot = ctypes.windll.kernel32.GetTickCount()
                elapsed_ms = millis_since_boot - lii.dwTime
                return max(0.0, elapsed_ms / 1000.0)
        except Exception as e:
            logger.debug(f"Could not query GetLastInputInfo: {e}")

        return 999.0

    def get_system_metrics(self) -> Dict[str, Any]:
        """Collects live laptop hardware and OS metrics."""
        # Disk usage for the drive holding the project storage
        try:
            disk_info = psutil.disk_usage(str(STORAGE_DIR))
            free_gb = disk_info.free / (1024 ** 3)
            total_gb = disk_info.total / (1024 ** 3)
            disk_percent = disk_info.percent
        except Exception:
            free_gb = 50.0
            total_gb = 100.0
            disk_percent = 50.0

        # Battery / Power
        battery = psutil.sensors_battery()
        if battery is not None:
            on_ac = battery.power_plugged if battery.power_plugged is not None else True
            bat_pct = battery.percent
        else:
            # Desktop or unavailable sensor -> assume plugged in
            on_ac = True
            bat_pct = 100.0

        # CPU load
        cpu_pct = psutil.cpu_percent(interval=None)

        # Idle time
        idle_secs = self.get_user_idle_seconds()
        is_user_active = idle_secs < self.policy.idle_threshold_seconds

        return {
            "free_disk_gb": round(free_gb, 2),
            "total_disk_gb": round(total_gb, 2),
            "disk_percent": round(disk_percent, 1),
            "on_ac_power": on_ac,
            "battery_percent": bat_pct,
            "cpu_percent": round(cpu_pct, 1),
            "idle_seconds": round(idle_secs, 1),
            "is_user_active": is_user_active
        }

    def can_process_heavy_task(self) -> Tuple[bool, str]:
        """Determines whether heavy tasks (transcription, AI, FFmpeg rendering) are permitted right now.
        
        Returns:
            (can_run: bool, reason: str)
        """
        metrics = self.get_system_metrics()

        # 1. Disk Space Safety Check (Critical hard stop)
        if metrics["free_disk_gb"] < self.policy.min_free_disk_gb:
            return False, (
                f"Disk space low ({metrics['free_disk_gb']} GB free). "
                f"Minimum required is {self.policy.min_free_disk_gb} GB."
            )

        # 2. Battery / Power Check
        if not metrics["on_ac_power"]:
            if not self.policy.allow_on_battery:
                return False, "Laptop running on battery. Waiting for AC power connection."
            if metrics["battery_percent"] < self.policy.min_battery_percent:
                return False, (
                    f"Battery too low ({metrics['battery_percent']}%), "
                    f"minimum threshold is {self.policy.min_battery_percent}%."
                )

        # 3. User Activity Check
        if self.policy.pause_when_user_active and metrics["is_user_active"]:
            return False, f"User is actively using laptop ({metrics['idle_seconds']}s idle). Deferring heavy jobs."

        # 4. CPU Saturation Check
        if metrics["cpu_percent"] > self.policy.max_cpu_percent:
            return False, f"CPU saturation too high ({metrics['cpu_percent']}% > {self.policy.max_cpu_percent}%)."

        return True, "All laptop resource conditions satisfied."

    def get_optimal_worker_threads(self) -> int:
        """Calculates thread count to assign to compute engines without starving the system."""
        total_cores = os.cpu_count() or 4
        # Keep reserved cores free
        assigned = max(1, total_cores - self.policy.reserved_cpu_cores)
        return assigned

    def set_low_process_priority(self, pid: Optional[int] = None) -> bool:
        """Sets the priority of this process or target PID to BELOW_NORMAL to keep UI responsive."""
        try:
            target_pid = pid or os.getpid()
            proc = psutil.Process(target_pid)
            if self._is_windows:
                proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            else:
                proc.nice(10)  # POSIX nice value (+10 is lower priority)
            logger.info(f"Adjusted process priority for PID {target_pid} to low/background.")
            return True
        except Exception as e:
            logger.debug(f"Unable to adjust process priority: {e}")
            return False
