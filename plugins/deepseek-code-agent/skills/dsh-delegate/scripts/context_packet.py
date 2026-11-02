"""Build a bounded, explicit context packet; never read Codex internal history."""
import hashlib
from pathlib import Path

MAX_FILE = 64 * 1024
MAX_PACKET = 160 * 1024
BLOCKED_DIRECTORIES = {
    '.git', '.ssh', '.aws', '.azure', '.config/gcloud', '.kube', '.docker',
    '.gnupg', '.password-store', '.huashu-chrome', '.netrc', '.npmrc',
}
BLOCKED_NAMES = {
    'credentials.txt', 'secrets.txt', 'credentials.md', 'secrets.md',
    'secret.md', 'tokens.md', 'id_rsa', 'id_ed25519',
}


def read_selected(path):
    path = Path(path).expanduser().resolve()
    lowered = [part.lower() for part in path.parts]
    if path.suffix.lower() not in ('.md', '.txt'):
        raise ValueError('Context must be explicitly selected Markdown/text files')
    for index in range(len(lowered)):
        for blocked in BLOCKED_DIRECTORIES:
            parts = blocked.split('/')
            if lowered[index:index + len(parts)] == parts:
                raise ValueError('Context must stay outside credential and internal-state directories')
    if any(
        a == '.codex' and b in ('sessions', 'archived_sessions', 'memories_v2')
        for a, b in zip(lowered, lowered[1:])
    ) or path.name.lower() in BLOCKED_NAMES:
        raise ValueError('Context must stay outside credential and internal-state directories')
    if not path.is_file() or path.stat().st_size > MAX_FILE:
        raise ValueError('Selected context is missing or exceeds 64 KiB')
    text = path.read_text(encoding='utf-8')
    return {'path': str(path), 'sha256': hashlib.sha256(text.encode()).hexdigest(), 'bytes': len(text.encode())}, text


def write_scope(workspace, paths):
    workspace = Path(workspace).resolve()
    out = []
    for item in paths:
        if not isinstance(item, str) or not item.strip():
            raise ValueError('Write scope entries must be non-empty strings')
        target = Path(item)
        if target.is_absolute() or '..' in target.parts or any(c in item for c in '*?['):
            raise ValueError('Write scope requires workspace-relative exact files/directories')
        resolved = (workspace / target).resolve()
        try:
            relative = resolved.relative_to(workspace)
        except ValueError:
            raise ValueError('Write scope escapes the workspace') from None
        out.append(str(relative))
    return list(dict.fromkeys(out))


def rules_for_scope(workspace, paths):
    workspace = Path(workspace).resolve()
    found = []
    for name in ['.'] + paths:
        target = workspace / name
        current = target if target.is_dir() else target.parent
        dirs = []
        while current == workspace or workspace in current.parents:
            dirs.append(current)
            if current == workspace:
                break
            current = current.parent
        for directory in reversed(dirs):
            rule = directory / 'AGENTS.md'
            if rule.is_file() and str(rule) not in found:
                found.append(str(rule))
    return found


def packet(config, message):
    workspace = config['cwd']
    paths = ([] if config.get('native_rules') else rules_for_scope(workspace, config['allow_write'])) + config['rule_files'] + config['context_files'] + message.get('context_files', [])
    sources, parts = [], []
    for path in dict.fromkeys(paths):
        meta, text = read_selected(path)
        sources.append(meta)
        parts.append(f"SOURCE {meta['path']}\nSHA256 {meta['sha256']}\n---\n{text}\n---")
    content = (
        'CONTROLLER TASK PACKET\n'
        f"WORKSPACE: {workspace}\n"
        f"ALLOWED WRITE PATHS: {config['allow_write'] or 'none; read-only task'}\n"
        'These paths are task constraints, not an OS-enforced allowlist. Preserve other changes.\n'
        'Read the selected project rules below. Only the current task and selected sources are shared; '
        'do not inspect Codex internal state, other conversations, or credentials. '
        'If the task requires permissions outside this scope, report the gap. '
        'For a resumed session, use this fresh packet for current context; do not silently rely on an obsolete source.\n\n'
        f"CURRENT MESSAGE {message['id']}:\n{message['text']}\n\n"
        + '\n\n'.join(parts)
        + '\n\nOwn implementation, relevant testing, fixing failures, and resource cleanup. '
        'REPORT: result; changed files; actual checks and results; unresolved blockers. '
        'Make routine decisions yourself; involve the parent only for a genuine blocker or missing decision.\n'
    )
    if len(content.encode()) > MAX_PACKET:
        raise ValueError('Context packet exceeds 160 KiB; select narrower sources')
    return content, sources
