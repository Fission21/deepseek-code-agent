"""Validation shared by the scheduler, runner and watchdog (no model calls)."""
import hashlib
import json
import math
from pathlib import Path
import time

DEFAULT_LIMITS = {
    'max_failures': 2, 'max_elapsed_seconds': 1800,
    'progress_timeout_seconds': 180, 'tool_timeout_seconds': 300,
    'max_peer_messages': 12, 'max_peer_rounds': 2,
}


def limits(value=None):
    value = value or {}
    if not isinstance(value, dict) or set(value) - set(DEFAULT_LIMITS):
        raise ValueError('Unknown task limits')
    result = dict(DEFAULT_LIMITS, **value)
    for key, n in result.items():
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n <= 0:
            raise ValueError('Limits must be positive finite numbers')
        if key in ('max_failures', 'max_peer_messages', 'max_peer_rounds') and not isinstance(n, int):
            raise ValueError('Count limits must be integers')
    return result


def acceptance(value, task, workspace):
    value = value or {'criteria': [task], 'commands': [], 'paths': []}
    if not isinstance(value, dict) or set(value) - {'criteria', 'commands', 'paths'}:
        raise ValueError('Invalid acceptance specification')
    criteria = value.get('criteria', [task])
    if not isinstance(criteria, list) or not criteria or any(not isinstance(s, str) or not s.strip() for s in criteria):
        raise ValueError('Acceptance criteria must be nonempty strings')
    commands = value.get('commands', [])
    if not isinstance(commands, list) or len(commands) > 20:
        raise ValueError('At most 20 acceptance commands')
    for argv in commands:
        if not isinstance(argv, list) or not argv or any(not isinstance(s, str) or '\0' in s for s in argv) or not argv[0]:
            raise ValueError('Acceptance commands must be argv arrays, not shell strings')
    paths = value.get('paths', [])
    if not isinstance(paths, list):
        raise ValueError('Acceptance paths must be a list')
    base = Path(workspace).resolve()
    for name in paths:
        if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Acceptance paths must be relative to the workspace')
        target = (base/name).resolve()
        if target != base and base not in target.parents:
            raise ValueError('Acceptance path escapes the workspace')
    if commands and not paths:
        raise ValueError('Acceptance commands require explicit source paths to bind evidence')
    return {'criteria': criteria, 'commands': commands, 'paths': paths}


def fingerprint(workspace, names):
    """Digest explicit source paths, including missing files; reject escaping symlinks."""
    base = Path(workspace).resolve()
    files = {}
    for name in names:
        p = base/name
        if p.is_symlink():
            raise ValueError('Acceptance paths cannot be symlinks')
        entries = sorted(p.rglob('*')) if p.is_dir() else [p]
        for item in entries:
            if any(part in ('.git', '__pycache__', 'node_modules') for part in item.relative_to(base).parts):
                continue
            target = item.resolve()
            if target != base and base not in target.parents or item.is_symlink():
                raise ValueError('Acceptance path contains a symlink')
            if item.is_dir():
                continue
            files[str(item.relative_to(base))] = hashlib.sha256(item.read_bytes()).hexdigest() if item.exists() else None
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def stalled(root, state, task_limits, now=None):
    now = time.time() if now is None else now
    active = state.get('active') or {}
    since = state.get('progress_at') or active.get('started_at')
    if not since:
        return None  # older sessions remain governed by their existing turn deadline
    if now - state.get('heartbeat_at', since) > 15:
        return 'Worker heartbeat expired'
    for file in (Path(root)/'mailbox/questions').glob('*.json'):
        try:
            q = json.loads(file.read_text())
        except (OSError, ValueError):
            continue
        if (q.get('invocation') == active.get('result_file') and q.get('status') == 'pending'
                and now * 1000 - q.get('at', 0) < 180000):
            return None
    tool_since = state.get('tool_started_at')
    if tool_since:
        if now - tool_since > task_limits['tool_timeout_seconds']:
            return 'Tool deadline expired; outcome may be uncertain'
    elif now - since > task_limits['progress_timeout_seconds']:
        return 'No execution progress before deadline'
    return None
