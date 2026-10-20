from __future__ import annotations

import ctypes
import hashlib
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


@dataclass(frozen=True)
class WorkerState:
    running: bool
    pid: int | None = None


def _pid_file(settings: Settings) -> Path:
    return getattr(settings, 'worker_dir', settings.data_dir) / "worker.pid"


def worker_signature(settings: Settings) -> str:
    """Fingerprint the code that determines worker behavior."""
    digest = hashlib.sha256()
    source_files = [settings.root / "scripts" / "worker.py"]
    source_files.extend(sorted((settings.root / "src" / "bike_publisher").glob("*.py")))
    for path in source_files:
        digest.update(path.relative_to(settings.root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return digest.hexdigest()


def _signature_file(settings: Settings) -> Path:
    return getattr(settings, 'worker_dir', settings.data_dir) / "worker.signature"


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(process_query, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_uint32()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def worker_state(settings: Settings) -> WorkerState:
    path = _pid_file(settings)
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return WorkerState(False)
    if _process_is_running(pid):
        return WorkerState(True, pid)
    try:
        path.unlink()
    except OSError:
        pass
    return WorkerState(False)


def stop_worker(settings: Settings, timeout_seconds: float = 5) -> None:
    current = worker_state(settings)
    if not current.running or current.pid is None:
        return

    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.TerminateProcess.restype = ctypes.c_int
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        # Waiting for termination needs SYNCHRONIZE as well as TERMINATE.
        process_terminate = 0x0001 | 0x00100000
        handle = kernel32.OpenProcess(process_terminate, False, current.pid)
        if not handle:
            raise OSError(ctypes.get_last_error(), "Не удалось остановить worker")
        try:
            if not kernel32.TerminateProcess(handle, 1):
                raise OSError(ctypes.get_last_error(), "Не удалось остановить worker")
            wait_result = kernel32.WaitForSingleObject(handle, int(timeout_seconds * 1000))
            if wait_result != 0:
                raise RuntimeError("Worker ещё не завершился; новый процесс не запущен")
        finally:
            kernel32.CloseHandle(handle)
    else:
        os.kill(current.pid, signal.SIGTERM)

    path = _pid_file(settings)
    try:
        if path.read_text(encoding="utf-8").strip() == str(current.pid):
            path.unlink()
    except OSError:
        pass


def ensure_worker_running(
    settings: Settings,
    *,
    restart: bool = False,
) -> tuple[WorkerState, bool]:
    current = worker_state(settings)
    if current.running:
        try:
            running_signature = _signature_file(settings).read_text(encoding="utf-8").strip()
        except OSError:
            running_signature = ""
        code_changed = running_signature != worker_signature(settings)
        if not restart and not code_changed:
            return current, False
        stop_worker(settings)

    runtime = getattr(settings, 'worker_dir', settings.data_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    log_path = settings.data_dir / "logs" / (f"worker-{settings.account_slot}.log" if getattr(settings, 'account_slot', '') else 'worker.log')
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_stream = log_path.open("a", encoding="utf-8")
    options: dict = {
        "cwd": str(settings.root),
        "stdout": log_stream,
        "stderr": subprocess.STDOUT,
    }
    worker_environment = os.environ.copy()
    worker_environment.update(
        {
            "BIKEBOT_DATA_DIR": str(settings.data_dir),
            "BIKEBOT_ACCOUNT_SLOT": getattr(settings, 'account_slot', ''),
            "BIKEBOT_BROWSER_PROFILE": str(settings.browser_profile),
            "BIKEBOT_BROWSER_CHANNEL": settings.browser_channel,
            "BIKEBOT_HEADLESS": "true" if settings.headless else "false",
            "BIKEBOT_ACTION_DELAY_SECONDS": str(
                getattr(settings, "action_delay_seconds", 2.5)
            ),
            "BIKEBOT_BETWEEN_POSTS_SECONDS": str(
                getattr(settings, "between_posts_seconds", 60)
            ),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    options["env"] = worker_environment
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            [sys.executable, str(settings.root / "scripts" / "worker.py")],
            **options,
        )
    finally:
        log_stream.close()
    _pid_file(settings).write_text(str(process.pid), encoding="utf-8")
    _signature_file(settings).write_text(worker_signature(settings), encoding="utf-8")
    return WorkerState(True, process.pid), True
