"""Cancelable subprocesses without visible console windows on Windows."""

import os
import signal
import subprocess
import threading
import time


class CancelledError(Exception):
    pass


class CancelToken:
    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._process = None

    def check(self):
        if self._event.is_set():
            raise CancelledError()

    def attach(self, process):
        with self._lock:
            self._process = process
            if self._event.is_set():
                self._kill(process)

    def detach(self, process):
        with self._lock:
            if self._process is process:
                self._process = None

    def cancel(self):
        self._event.set()
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._kill(self._process)

    @staticmethod
    def _kill(process, force_group=False):
        if process.poll() is not None and not force_group:
            return
        try:
            if os.name == "nt":
                subprocess.Popen(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            process.kill()
        except OSError:
            pass


def _drain_stopped(process):
    """Never wait indefinitely for descendants holding the output pipes open."""
    try:
        process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        CancelToken._kill(process, force_group=True)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def run_command(command, *, input=None, timeout=None, token=None):
    """Return CompletedProcess; cancellation kills the running child process."""
    if token:
        token.check()
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
             if os.name == "nt" else 0)
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", creationflags=flags,
        startupinfo=startupinfo, start_new_session=os.name != "nt",
    )
    if token:
        token.attach(process)
    deadline = time.monotonic() + timeout if timeout is not None else None
    try:
        pending_input = input
        while True:
            if token:
                token.check()
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                CancelToken._kill(process)
                _drain_stopped(process)
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(
                    input=pending_input,
                    timeout=min(0.2, remaining) if remaining is not None else 0.2,
                )
                break
            except subprocess.TimeoutExpired:
                pending_input = None
        if token:
            token.check()
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    finally:
        if process.poll() is None:
            CancelToken._kill(process)
            _drain_stopped(process)
        if token:
            token.detach(process)
