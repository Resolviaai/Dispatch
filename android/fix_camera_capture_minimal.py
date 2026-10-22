"""Minimal compatibility repair for Dispatch CameraCaptureManager.

Run from C:\\CODE\\Dispatch\\android. The script only updates
CameraCaptureManager.kt, creates a .pre-fix.bak backup, and refuses to run
if the expected source patterns do not match exactly.
"""
from pathlib import Path
import shutil

RELATIVE = Path("app/src/main/java/com/resolvia/dispatch/recorder/CameraCaptureManager.kt")
if not RELATIVE.is_file():
    raise SystemExit(
        "CameraCaptureManager.kt not found. Run this script from C:\\CODE\\Dispatch\\android."
    )

raw = RELATIVE.read_bytes()
newline = "\r\n" if b"\r\n" in raw else "\n"
source = raw.decode("utf-8").replace("\r\n", "\n")
original = source


def replace_once(old: str, new: str, label: str) -> None:
    global source
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"Expected one occurrence for {label}, found {count}. No file was written.")
    source = source.replace(old, new, 1)

# Keep the helper signature/semantics already used by the checked-in unit tests.
replace_once(
    "internal fun isAeAfLockConfirmed(afLockConfirmed: Boolean, aeLockConfirmed: Boolean): Boolean =\n"
    "    afLockConfirmed || aeLockConfirmed",
    "internal fun isAeAfLockConfirmed(hasAf: Boolean, hasAe: Boolean, focusSuccessful: Boolean): Boolean =\n"
    "    hasAe || (hasAf && focusSuccessful)",
    "AE/AF confirmation helper",
)

# Restore the previous mutable combined lock-state contract. The current callers and
# native bridge consume isAeAfLocked as one Boolean; the split state was only partly applied.
replace_once(
    "    @Volatile var isAeLocked: Boolean = false\n"
    "        private set\n"
    "    @Volatile var isAfLocked: Boolean = false\n"
    "        private set\n"
    "    @Volatile var isAeAfLockUncertain: Boolean = false\n"
    "        private set\n"
    "    val isAeAfLocked: Boolean\n"
    "        get() = isAeAfLockConfirmed(isAfLocked, isAeLocked)",
    "    @Volatile var isAeAfLocked: Boolean = false\n"
    "        private set\n\n"
    "    private fun clearConfirmedLockState() {\n"
    "        isAeAfLocked = false\n"
    "    }",
    "combined AE/AF lock-state property",
)

# CameraRequestGeneration intentionally exposes begin()/isCurrent(), not AtomicLong methods.
old_begin_count = source.count("focusOperationGeneration.incrementAndGet()")
old_get_count = source.count("focusOperationGeneration.get()")
if old_begin_count == 0 or old_get_count == 0:
    raise SystemExit(
        "Expected the incomplete CameraRequestGeneration API calls were not found. "
        "No file was written; inspect the current source first."
    )
source = source.replace("focusOperationGeneration.incrementAndGet()", "focusOperationGeneration.begin()")
source = source.replace(
    "focusOperationGeneration.get() != operationGeneration",
    "!focusOperationGeneration.isCurrent(operationGeneration)",
)
source = source.replace(
    "focusOperationGeneration.get() == operationGeneration",
    "focusOperationGeneration.isCurrent(operationGeneration)",
)

# Fail closed if any incompatible declarations/calls remain.
checks = {
    "three-argument helper": "internal fun isAeAfLockConfirmed(hasAf: Boolean, hasAe: Boolean, focusSuccessful: Boolean): Boolean",
    "combined mutable lock state": "@Volatile var isAeAfLocked: Boolean = false",
    "lock reset helper": "private fun clearConfirmedLockState()",
}
for label, needle in checks.items():
    if needle not in source:
        raise SystemExit(f"Validation failed: missing {label}. No file was written.")
if "isAeAfLocked = isAeAfLockConfirmed(isAfLocked, isAeLocked)" in source:
    raise SystemExit("Validation failed: computed read-only lock property remains. No file was written.")
if "focusOperationGeneration.incrementAndGet()" in source or "focusOperationGeneration.get()" in source:
    raise SystemExit("Validation failed: legacy generation calls remain. No file was written.")
if source == original:
    raise SystemExit("No changes produced. No file was written.")

backup = RELATIVE.with_suffix(RELATIVE.suffix + ".pre-fix.bak")
if backup.exists():
    raise SystemExit(
        f"Backup already exists at {backup}. No file was written. Preserve that backup and inspect the current file first."
    )
shutil.copy2(RELATIVE, backup)
RELATIVE.write_bytes(source.replace("\n", newline).encode("utf-8"))
print(f"Patched: {RELATIVE}")
print(f"Backup:  {backup}")
print("Scope: CameraCaptureManager.kt only; no camera algorithm bodies were rewritten.")
print("Next: run .\\gradlew.bat assembleDebug and .\\gradlew.bat testDebugUnitTest.")
