"""Persistent DSH conversation controller. Idle workers keep files, not a daemon."""
import argparse
from contextlib import contextmanager
try:
    import fcntl
except ImportError:  # Windows: reported by require_supported, never as a traceback.
    fcntl = None
import json
import math
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit

from context_packet import packet, read_selected, write_scope
from paths import require_supported, resolve_dsh, resolve_huashu, resolve_node
from process_identity import group_has_owner, has_owner

HERE = Path(__file__).resolve().parent
FORMAT = 'dsh-delegate-worker-v1'


def save(path, value):
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_state(root):
    state = json.loads((root / 'state.json').read_text())
    if state.get('format') != FORMAT:
        raise ValueError('Not a DSH delegate worker directory')
    return state


@contextmanager
def locked(root):
    # Every command validates ownership before opening the lock file.
    require_supported()
    read_state(root)
    with open(root / 'state.lock', 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        state = read_state(root)
        try:
            yield state
        except BaseException:
            raise
        else:
            state['revision'] += 1
            save(root / 'state.json', state)


def compact(root):
    state = read_state(root)
    return {key: state.get(key) for key in (
        'state', 'revision', 'session_id', 'completed_turns', 'active', 'last_result', 'last_event', 'error', 'controller_pid'
    )} | {'worker_dir': str(root), 'pending_turns': len(state['queue'])}


def message_from(args):
    _, text = read_selected(args.task_file)
    context = [str(Path(x).expanduser().resolve()) for x in args.context_file]
    for path in context:
        read_selected(path)
    if not text.strip():
        raise ValueError('Task is empty')
    return {'text': text, 'context_files': context}


def launch(root):
    # Multiple launchers serialize on worker.lock. No idle polling service remains.
    require_supported()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    proc = subprocess.Popen([sys.executable, str(HERE / 'worker.py'), '_drain', '--state-dir', str(root)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, env=env)
    threading.Thread(target=proc.wait, daemon=True).start()


def _process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _ps(pid, fields):
    """Optional stricter identity check; `ps` is unavailable in some sandboxes."""
    try:
        return subprocess.run(
            ['ps', '-p', str(pid), '-o', fields], capture_output=True, text=True, timeout=5,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
        )
    except (OSError, subprocess.SubprocessError):
        return None


def runner_alive(active):
    if not active:
        return False
    pid = active.get('runner_pid')
    if not isinstance(pid, int) or not _process_alive(pid):
        return False
    token = active.get('owner_token')
    if isinstance(token, str) and token:
        if not has_owner(pid, token):
            return False
    else:
        # Legacy records have no invocation token. Without `ps` we cannot prove
        # this PID still belongs to the old runner, so fail closed.
        check = _ps(pid, 'command=')
        if check is None or check.returncode != 0:
            return False
        command = check.stdout
        if str(HERE / 'run_dsh.py') not in command or active['result_file'] not in command:
            return False
        return True
    # The environment token is strong identity. A successful `ps` check adds
    # command-level confirmation without being required in restricted sandboxes.
    check = _ps(pid, 'command=')
    if check is not None and check.returncode == 0:
        return str(HERE / 'run_dsh.py') in check.stdout and active['result_file'] in check.stdout
    return True


def stop_runner(active, force=False):
    if runner_alive(active):
        try:
            os.kill(active['runner_pid'], signal.SIGKILL if force else signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


def _group_exists(pgid):
    if not isinstance(pgid, int) or pgid <= 0:
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def owned_group_alive(active):
    """Verify ownership by the invocation token, never by PID alone.

    The leader may already be gone while descendants still carry the token in
    their environment. Group membership is read from /proc on Linux and from
    `ps` on macOS; if it cannot be read, return False so the caller fails closed.
    """
    pid = active.get('child_pid') if active else None
    token = active.get('owner_token') if active else None
    if not isinstance(pid, int) or not isinstance(token, str) or not token:
        return False
    if _process_alive(pid) and has_owner(pid, token):
        return True
    return bool(group_has_owner(pid, token))


def stop_owned_group(active, trusted=False):
    """Stop a recorded group; return True only when the group is gone.

    ``trusted`` is used only by the drain that still owns the launcher Popen.
    Other callers must provide a live invocation token; an unverifiable stale
    record returns False without signaling anything.
    """
    pid = active.get('child_pid') if active else None
    if not isinstance(pid, int) or pid <= 0:
        return True
    if not trusted and not owned_group_alive(active):
        return not _group_exists(pid)
    if trusted and not _group_exists(pid):
        return True
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    deadline = time.monotonic() + 3
    while _group_exists(pid) and time.monotonic() < deadline:
        time.sleep(.1)
    if _group_exists(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        deadline = time.monotonic() + 2
        while _group_exists(pid) and time.monotonic() < deadline:
            time.sleep(.1)
    return not _group_exists(pid)


def close_output(reader, stream):
    """Close stdout only after its reader has stopped.

    A TextIOWrapper.close() can wait for a read lock held by our daemon reader.
    If a still-running descendant holds the pipe, closing from this thread would
    deadlock.  The child group is stopped first; if the reader is still blocked,
    leave the daemon and descriptor to process exit instead of hanging here.
    """
    if reader is not None:
        reader.join(timeout=2)
        if reader.is_alive():
            return
    try:
        if stream is not None:
            stream.close()
    except (OSError, ValueError):
        pass


def recover_orphan(root):
    # Only the holder of the controller lease may reconcile an orphan.
    require_supported()
    with open(root / 'worker.lock', 'a') as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        state = read_state(root)
        active = state['active']
        if not active:
            return True
        stop_runner(active)
        deadline = time.monotonic() + 5
        while runner_alive(active) and time.monotonic() < deadline:
            time.sleep(.1)
        if runner_alive(active):
            # A stuck launcher must not keep its model process alive forever.
            stop_runner(active, force=True)
            deadline = time.monotonic() + 2
            while runner_alive(active) and time.monotonic() < deadline:
                time.sleep(.1)
        cleaned = stop_owned_group(active)
        if runner_alive(active):
            with locked(root) as state:
                state['state'] = 'needs_attention'
                state['error'] = 'Owned runner is still stopping; wait, then stop again'
            return True
        if not cleaned and _group_exists(active.get('child_pid')):
            with locked(root) as state:
                state['state'] = 'needs_attention'
                state['error'] = (
                    'Could not verify ownership of the recorded process group; no signal '
                    'was sent. Inspect the retained turn before continuing.'
                )
            return True
        result = None
        result_path = Path(active['result_file'])
        # Results are expected inside this worker; do not read arbitrary state paths.
        if result_path.parent == root / 'turns' and result_path.is_file():
            try:
                result = json.loads(result_path.read_text())
            except ValueError:
                pass
        with locked(root) as state:
            message = state.get('inflight')
            if message:
                save(root / 'turns' / f"{message['id']:05d}.message.json", message)
            if result and result.get('session_id'):
                if not state['session_id'] or state['session_id'] == result['session_id']:
                    state['session_id'] = result['session_id']
            state['last_result'] = {'turn': active['turn'], 'path': str(result_path),
                                    'status': result.get('status') if result else 'interrupted_uncertain'}
            state['active'] = None
            state['inflight'] = None
            state['controller_pid'] = None
            state['state'] = 'closed' if state['closed'] else 'paused'
            state['error'] = 'Orphan stopped; inspect the retained turn before sending a correction. It was not replayed.'
        gate = root / 'turns' / f"{active['turn']:05d}.gate"
        if gate.is_file():
            gate.unlink()
        return True


def drain(root):
    require_supported()
    read_state(root)
    with open(root / 'worker.lock', 'a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX)
        config = json.loads((root / 'config.json').read_text())
        proc = None
        while True:
            with locked(root) as state:
                if state['active']:
                    # A crashed controller must not replay an uncertain write.
                    state['state'] = 'needs_attention'
                    state['paused'] = True
                    state['error'] = 'Previous active turn needs inspection; stop it and inspect its report before continuing'
                    return
                if state['closed'] or state['paused'] or not state['queue']:
                    if config.get('controller_link') and not state['closed'] and not state['paused']:
                        # A note that arrived after the last native model boundary
                        # becomes a normal follow-up instead of getting stranded.
                        files = sorted((root / 'mailbox/inbox').glob('*.json'))
                        received = state.setdefault('controller_received', [])
                        for file in files:
                            note = json.loads(file.read_text())
                            if note['id'] not in received:
                                state['queue'].append({'id': state['next_id'], 'text': note['message'], 'context_files': [], 'controller_message_id': note['id']})
                                state['next_id'] += 1
                                received.append(note['id'])
                        # Commit before acknowledging files; receipt IDs prevent
                        # duplicate turns if the controller exits between them.
                        save(root / 'state.json', state)
                        for file in files:
                            file.rename(root / 'mailbox/delivered' / file.name)
                    if state['queue'] and not state['closed'] and not state['paused']:
                        continue
                    if not state['closed'] and not state['paused']:
                        state['state'] = 'idle'
                    state['controller_pid'] = None
                    return
                message = state['queue'].pop(0)
                number = message['id']
                state['state'] = 'running'
                session_id = state['session_id']
                state['controller_pid'] = os.getpid()
                state['inflight'] = message
                state['active'] = {'turn': number, 'runner_pid': None, 'child_pid': None,
                                   'result_file': str(root / 'turns' / f'{number:05d}.result.json'),
                                   'phase': 'preparing'}
            name = f'{number:05d}'
            result_file = root / 'turns' / (name + '.result.json')
            events_file = root / 'turns' / (name + '.events.jsonl')
            task_file = root / 'turns' / (name + '.task.txt')
            gate_file = root / 'turns' / (name + '.gate')
            reader = None
            try:
                save(root / 'turns' / (name + '.message.json'), message)
                body, sources = packet(config, message)
                with open(task_file, 'x', encoding='utf-8') as f:
                    os.chmod(task_file, 0o600)
                    f.write(body)
                save(root / 'turns' / (name + '.context.json'), sources)
                cmd = [sys.executable, str(HERE / 'run_dsh.py'), '--cwd', config['cwd'],
                       '--task-file', str(task_file), '--result-file', str(result_file),
                       '--events-file', str(events_file), '--dsh', config['dsh'],
                       '--timeout', str(config['turn_timeout']), '--start-gate', str(gate_file)]
                if session_id:
                    cmd += ['--session-id', session_id]
                if config.get('controller_link'):
                    cmd += ['--controller-mailbox', str(root / 'mailbox')]
                if config['tab_id'] is not None:
                    cmd += ['--tab-id', str(config['tab_id']), '--url', config['url']]
                    if config['huashu_entry']:
                        cmd += ['--huashu-entry', config['huashu_entry']]
                owner_token = uuid.uuid4().hex
                with locked(root) as state:
                    if state['paused'] or state['closed']:
                        state['queue'].insert(0, message)
                        state['inflight'] = None
                        state['active'] = None
                        state['controller_pid'] = None
                        # No process ran; remove only these new invocation files.
                        task_file.unlink()
                        (root / 'turns' / (name + '.context.json')).unlink()
                        return
                    proc = subprocess.Popen(
                        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True, start_new_session=True,
                        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                                 DSH_DELEGATE_OWNER=owner_token),
                    )
                    state['active'] = {'turn': number, 'runner_pid': proc.pid, 'child_pid': None,
                                       'owner_token': owner_token, 'result_file': str(result_file)}
                # Publish the gate only after the PID/turn record has been committed.
                with open(gate_file, 'x') as gate:
                    os.chmod(gate_file, 0o600)
                    gate.write('registered\n')
                # Read stdout in a thread so a killed launcher cannot wedge the
                # controller when a surviving grandchild still holds the pipe.
                events = queue.Queue()

                def read_output(stream=proc.stdout):
                    try:
                        for line in stream:
                            events.put(line)
                    except (OSError, ValueError):
                        pass
                    finally:
                        events.put(None)

                reader = threading.Thread(target=read_output, daemon=True)
                reader.start()
                while True:
                    try:
                        line = events.get(timeout=.5)
                    except queue.Empty:
                        if proc.poll() is None:
                            continue
                        try:
                            line = events.get(timeout=.5)
                        except queue.Empty:
                            break
                    if line is None:
                        break
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    with locked(root) as state:
                        state['last_event'] = event
                        active = state.get('active')
                        if (active and active.get('turn') == number
                                and event.get('status') == 'started'
                                and isinstance(event.get('pid'), int)):
                            # Remember the DSH child's private process group so a
                            # killed launcher cannot leave it running.
                            active['child_pid'] = event['pid']
                        if event.get('type') == 'session':
                            if state['session_id'] and state['session_id'] != event.get('session_id'):
                                raise ValueError('Session identity changed')
                            state['session_id'] = event.get('session_id')
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                # The launcher is gone. Clean the recorded child group before
                # joining/closing stdout: a surviving grandchild may still hold
                # the pipe and a synchronous close would wait on the reader lock.
                try:
                    stop_owned_group(read_state(root).get('active'), trusted=True)
                except (OSError, ValueError):
                    pass
                close_output(reader, proc.stdout)
                report = json.loads(result_file.read_text())
                with locked(root) as state:
                    state['active'] = None
                    state['inflight'] = None
                    state['last_result'] = {'turn': number, 'path': str(result_file), 'status': report['status']}
                    if report.get('session_id'):
                        if state['session_id'] and state['session_id'] != report['session_id']:
                            raise ValueError('Result session identity changed')
                        state['session_id'] = report['session_id']
                    if report['status'] == 'completed':
                        state['completed_turns'] += 1
                    elif not state['closed']:
                        state['paused'] = True
                        state['state'] = 'paused'
                        state['error'] = 'Turn did not complete; inspect report before sending a correction'
                proc = None
            except BaseException as exc:
                if proc is not None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                # The launcher may have been killed without running its finally
                # block.  Stop the recorded child group before touching stdout.
                try:
                    stop_owned_group(read_state(root).get('active'), trusted=True)
                except (OSError, ValueError):
                    pass
                if proc is not None:
                    close_output(reader, proc.stdout)
                with locked(root) as state:
                    state['active'] = None
                    state['inflight'] = None
                    state['controller_pid'] = None
                    state['state'] = 'closed' if state['closed'] else 'paused'
                    state['paused'] = True
                    state['error'] = type(exc).__name__ + ': turn stopped; no automatic replay'
                    state['last_result'] = {'turn': number, 'path': str(result_file), 'status': 'controller_error'}
                return
            finally:
                if gate_file.exists():
                    gate_file.unlink()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('start', 'send', 'status', 'wait', 'stop', 'close', '_drain'):
        p = sub.add_parser(command)
        p.add_argument('--state-dir', required=True, type=Path)
        if command in ('start', 'send'):
            p.add_argument('--task-file', required=True, type=Path)
            p.add_argument('--context-file', action='append', default=[])
            p.add_argument('--defer', action='store_true', help=argparse.SUPPRESS)
        if command == 'start':
            p.add_argument('--cwd', required=True, type=Path)
            p.add_argument('--rule-file', action='append', default=[])
            p.add_argument('--allow-write', action='append', default=[])
            p.add_argument('--dsh')
            p.add_argument('--turn-timeout', type=float, default=300)
            p.add_argument('--tab-id', type=int)
            p.add_argument('--url')
            p.add_argument('--huashu-entry')
            p.add_argument('--controller-link', action='store_true')
        if command == 'wait':
            p.add_argument('--timeout', type=float, default=30)
    return parser, parser.parse_args()


def main():
    parser, args = parse_args()
    require_supported()
    root = args.state_dir.expanduser().absolute()
    if args.command == '_drain':
        drain(root)
        return 0
    if args.command == 'start':
        workspace = args.cwd.expanduser().resolve()
        if not workspace.is_dir() or not math.isfinite(args.turn_timeout) or args.turn_timeout <= 0:
            parser.error('Existing workspace and positive finite turn-timeout required')
        if (args.tab_id is None) != (args.url is None):
            parser.error('Browser mode requires both tab-id and url')
        if args.tab_id is not None:
            parsed_url = urlsplit(args.url)
            if (args.tab_id <= 0 or parsed_url.scheme not in ('http', 'https')
                    or not parsed_url.hostname or parsed_url.username or parsed_url.password):
                raise ValueError('Use a positive tab ID and an http(s) URL without embedded credentials')
        try:
            dsh = resolve_dsh(args.dsh)
        except (OSError, ValueError) as exc:
            raise ValueError(str(exc)) from None
        huashu = None
        if args.tab_id is not None:
            huashu = str(resolve_huashu(args.huashu_entry))
            resolve_node()
        message = message_from(args)
        message['id'] = 1
        config = {'cwd': str(workspace), 'allow_write': write_scope(workspace, args.allow_write),
                  'rule_files': [str(Path(x).expanduser().resolve()) for x in args.rule_file],
                  'context_files': message['context_files'], 'dsh': dsh,
                  'turn_timeout': args.turn_timeout, 'tab_id': args.tab_id, 'url': args.url,
                  'huashu_entry': huashu}
        config['controller_link'] = args.controller_link
        config['native_rules'] = args.controller_link
        packet(config, message)  # Fail before creating state or calling a model.
        root.mkdir(mode=0o700, parents=False, exist_ok=False)
        (root / 'turns').mkdir(mode=0o700)
        if args.controller_link:
            for directory in ('inbox', 'delivered', 'outbox', 'questions', 'replies'):
                (root / 'mailbox' / directory).mkdir(parents=True, mode=0o700)
        save(root / 'config.json', config)
        save(root / 'state.json', {'format': FORMAT, 'state': 'queued', 'revision': 0,
              'session_id': None, 'queue': [message], 'next_id': 2, 'completed_turns': 0,
              'active': None, 'inflight': None, 'controller_pid': None, 'last_result': None, 'last_event': None, 'paused': False,
              'closed': False, 'error': None})
        if not args.defer:
            launch(root)
    elif args.command == 'send':
        message = message_from(args)
        config = json.loads((root / 'config.json').read_text())
        with locked(root) as state:
            if state['closed']:
                raise ValueError('Worker is closed; retained history is read-only')
            if state['paused'] and state['active']:
                raise ValueError('Worker is still stopping; wait for active=null before sending a correction')
            message['id'] = state['next_id']
            packet(config, message)
            state['next_id'] += 1
            state['queue'].append(message)
            state['paused'] = False
            state['error'] = None
            if not state['active']:
                state['state'] = 'queued'
        if not args.defer:
            launch(root)
    elif args.command in ('stop', 'close'):
        with locked(root) as state:
            state['paused'] = True
            if args.command == 'close':
                state['closed'] = True
            state['state'] = 'closed' if state['closed'] else 'paused'
            active = state['active']
        if not recover_orphan(root):
            stop_runner(active)
            # The drain may still hold the lease while a grandchild keeps its
            # stdout pipe open.  Stop only a group whose invocation token matches.
            cleaned = stop_owned_group(active)
            if not cleaned and _group_exists(active.get('child_pid')):
                with locked(root) as state:
                    state['state'] = 'needs_attention'
                    state['error'] = (
                        'Could not verify ownership of the recorded process group; no signal '
                        'was sent. Inspect the retained turn before continuing.'
                    )
    elif args.command == 'wait':
        if not math.isfinite(args.timeout) or not 0 <= args.timeout <= 55:
            parser.error('wait timeout must be between 0 and 55 seconds')
        end = time.monotonic() + args.timeout
        while time.monotonic() < end:
            state = read_state(root)
            if not state['active'] and state['state'] in ('idle', 'paused', 'closed', 'needs_attention'):
                break
            time.sleep(.1)
    print(json.dumps(compact(root), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': type(exc).__name__, 'detail': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
