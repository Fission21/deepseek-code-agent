"""Verify a recorded process really belongs to this invocation.

A PID stored on disk is not proof of ownership: PIDs can be reused.  The launcher
passes ``DSH_DELEGATE_OWNER`` to the process it starts, and this module reads the
process environment without invoking ``ps`` on Linux or macOS.  If the environment
cannot be read, callers must fail closed rather than signal a possibly unrelated
process.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys

OWNER_KEY = "DSH_DELEGATE_OWNER"


def _linux_environment(pid):
    try:
        data = (Path("/proc") / str(pid) / "environ").read_bytes()
    except OSError:
        return None
    return data.split(b"\0")


def _darwin_environment(pid):
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, int(pid))  # CTL_KERN, KERN_PROCARGS2
        size = ctypes.c_size_t(0)
        if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
            return None
        if size.value <= 0 or size.value > 4 * 1024 * 1024:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
            return None
        return buffer.raw[:size.value].split(b"\0")
    except (OSError, ValueError, ctypes.ArgumentError):
        return None


def environment(pid):
    if not isinstance(pid, int) or pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        return _linux_environment(pid)
    if sys.platform == "darwin":
        return _darwin_environment(pid)
    return None


def has_owner(pid, token):
    """Return True only when the live process carries this exact owner token."""
    if not isinstance(token, str) or not token:
        return False
    entries = environment(pid)
    if entries is None:
        return False
    expected = (OWNER_KEY + "=" + token).encode()
    return expected in entries


def _linux_group_members(pgid):
    members = []
    try:
        entries = list(Path("/proc").glob("[0-9]*"))
    except OSError:
        return None
    for entry in entries:
        try:
            stat = (entry / "stat").read_text(encoding="utf-8")
        except OSError:
            continue
        # The comm field may contain spaces or parentheses; fields after the last
        # ')' begin with state, ppid, pgrp.
        try:
            tail = stat.rsplit(")", 1)[1].split()
            if len(tail) > 2 and int(tail[2]) == pgid:
                members.append(int(entry.name))
        except (IndexError, ValueError):
            continue
    return members


def _macos_group_members(pgid):
    # `ps` is optional because some sandboxes block it. If unavailable, return
    # None so the caller fails closed instead of guessing ownership.
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=,pgid="],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    members = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) == pgid:
            try:
                members.append(int(parts[0]))
            except ValueError:
                pass
    return members


def group_members(pgid):
    """Return PIDs in a process group, or None when membership is unknown."""
    if not isinstance(pgid, int) or pgid <= 0:
        return None
    if sys.platform.startswith("linux"):
        return _linux_group_members(pgid)
    if sys.platform == "darwin":
        return _macos_group_members(pgid)
    return None


def group_has_owner(pgid, token):
    members = group_members(pgid)
    if members is None:
        return None
    return any(has_owner(pid, token) for pid in members)
