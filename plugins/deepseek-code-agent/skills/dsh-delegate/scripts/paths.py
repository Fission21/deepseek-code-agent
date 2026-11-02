"""Resolve DSH, Node, huashu-chrome and runtime paths from the install location.

No path in this module is tied to a particular user account.  Explicit flags and
environment variables win over discovery, which keeps the launcher usable when
DSH is installed somewhere unusual.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SUPPORTED_PLATFORMS = ("darwin", "linux")
DEFAULT_HUASHU_VERSION = "1.3.0"
_APP_CLI = Path("DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh")


class UnsupportedPlatform(ValueError):
    """Raised before any work starts on a platform without POSIX guarantees."""


def platform_info():
    return {
        "platform": sys.platform,
        "platform_supported": sys.platform in SUPPORTED_PLATFORMS,
        "supported_platforms": list(SUPPORTED_PLATFORMS),
        "reason": (
            None
            if sys.platform in SUPPORTED_PLATFORMS
            else "DSH execution uses POSIX advisory locks, process groups and `ps`; "
            "Windows is not supported yet. Run preflight there and use the legacy "
            "OpenCode lane or WSL only if that is an explicit user choice."
        ),
        "python": sys.executable,
        "python_version": "%d.%d.%d" % sys.version_info[:3],
    }


def require_supported():
    info = platform_info()
    if not info["platform_supported"]:
        raise UnsupportedPlatform(info["reason"])
    return info


def _is_executable(path):
    try:
        return path.is_file() and os.access(str(path), os.X_OK)
    except OSError:
        return False


def _candidate(value):
    if value is None or not str(value).strip():
        return None
    return Path(value).expanduser()


def _configured(label, value):
    candidate = _candidate(value)
    if candidate is None:
        return None
    return label, candidate


def resolve_dsh(explicit=None):
    """Return a configured usable DSH CLI, or discover one when none is set.

    An explicitly configured path is authoritative: a typo must be reported, not
    silently replaced by another DSH installation found on PATH.
    """
    for label, value in (
        ("--dsh", explicit),
        ("DSH_BIN", os.environ.get("DSH_BIN")),
        ("DSH_CLI", os.environ.get("DSH_CLI")),
    ):
        configured = _configured(label, value)
        if configured is None:
            continue
        label, candidate = configured
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        if _is_executable(resolved):
            return str(resolved)
        raise FileNotFoundError(
            "%s is set to %s, which is not an executable DSH CLI. Fix or unset it; "
            "no other installation is used as a fallback." % (label, candidate)
        )

    candidates = []
    found = shutil.which("dsh")
    if found:
        candidates.append(Path(found))
    home = Path.home()
    for base in (Path("/Applications"), home / "Applications"):
        candidates.append(base / _APP_CLI)
    # A checkout or a local install may expose the launcher here.
    candidates.append(home / ".local/bin/dsh")
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if _is_executable(resolved):
            return str(resolved)
    raise FileNotFoundError(
        "DSH CLI not found. Install DeepSeek Harness, pass --dsh PATH, or set DSH_BIN. "
        "Preflight does not open a DSH model session or call a model; it may run dsh --version."
    )


def resolve_node(explicit=None):
    for label, value in (("--node", explicit), ("NODE_BIN", os.environ.get("NODE_BIN"))):
        configured = _configured(label, value)
        if configured is None:
            continue
        label, candidate = configured
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        if _is_executable(resolved):
            # Return an absolute path: DSH later changes cwd to the task
            # workspace, which must not change how NODE_BIN resolves.
            return str(resolved)
        raise FileNotFoundError(
            "%s is set to %s, which is not an executable Node.js binary. Fix or unset it; "
            "no PATH fallback is used." % (label, candidate)
        )
    found = shutil.which("node")
    if found:
        return found
    raise FileNotFoundError(
        "Node.js was not found. Install Node.js 18+ or set NODE_BIN; the huashu-chrome "
        "MCP bridge runs through Node."
    )


def runtime_root_for(dsh_cli):
    """Locate the Electron @deepseek-ai module root that controller_link imports.

    The real app.asar is a file, so existence of the @deepseek-ai directory cannot
    be checked with normal Python filesystem calls.  We verify the app.asar file and
    let the DSH Electron runtime resolve paths inside it.
    """
    explicit = os.environ.get("DSH_RUNTIME_ROOT")
    if explicit:
        return str(Path(explicit).expanduser().absolute())
    try:
        cli = Path(dsh_cli).expanduser().resolve()
    except OSError:
        return None
    for parent in cli.parents:
        if (parent / "app.asar").is_file():
            return str(parent / "app.asar" / "dsh" / "node_modules" / "@deepseek-ai")
    return None


def huashu_metadata(entry):
    entry = Path(entry).expanduser().resolve()
    if not entry.is_file() or not os.access(str(entry), os.R_OK):
        raise ValueError("huashu-chrome CLI entry is missing or unreadable: %s" % entry)
    package_file = entry.parents[1] / "package.json"
    try:
        package = json.loads(package_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, IndexError):
        raise ValueError("huashu-chrome package metadata is missing or malformed") from None
    if package.get("name") != "huashu-chrome":
        raise ValueError("The selected entry is not the huashu-chrome package")
    return {"name": package["name"], "version": package.get("version", ""), "entry": str(entry)}


def _huashu_discovery():
    candidates = []
    npx = Path.home() / ".npm/_npx"
    if npx.is_dir():
        candidates.extend(sorted(npx.glob("*/node_modules/huashu-chrome/src/cli.js")))
    for base in (
        Path("/usr/local/lib/node_modules"),
        Path("/opt/homebrew/lib/node_modules"),
        Path.home() / ".npm-global/lib/node_modules",
    ):
        candidates.append(base / "huashu-chrome/src/cli.js")
    npm = shutil.which("npm")
    if npm:
        try:
            result = subprocess.run(
                [npm, "root", "-g"], capture_output=True, text=True, timeout=5,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            )
            if result.returncode == 0 and result.stdout.strip():
                candidates.append(Path(result.stdout.strip()) / "huashu-chrome/src/cli.js")
        except (OSError, subprocess.SubprocessError):
            pass
    return candidates


def _huashu_entry(candidate):
    if candidate.is_dir():
        return candidate / "src/cli.js"
    return candidate


def resolve_huashu(explicit=None, expected_version=None):
    expected = expected_version or os.environ.get("HUASHU_CHROME_VERSION") or DEFAULT_HUASHU_VERSION
    for label, value in (
        ("--huashu-entry", explicit),
        ("HUASHU_CHROME_ENTRY", os.environ.get("HUASHU_CHROME_ENTRY")),
    ):
        configured = _configured(label, value)
        if configured is None:
            continue
        label, candidate = configured
        entry = _huashu_entry(candidate)
        try:
            metadata = huashu_metadata(entry)
        except (OSError, ValueError) as exc:
            raise FileNotFoundError(
                "%s is set to %s, but it is not a readable huashu-chrome package: %s. "
                "Fix or unset it; no other installation is used as a fallback."
                % (label, candidate, exc)
            ) from None
        if metadata["version"] != expected:
            raise FileNotFoundError(
                "%s points to huashu-chrome %s, but this launcher expects %s. "
                "Fix the path, or set HUASHU_CHROME_VERSION only after checking the "
                "different installed version; no fallback is used."
                % (label, metadata["version"] or "unknown", expected)
            )
        return metadata["entry"]
    for candidate in _huashu_discovery():
        entry = _huashu_entry(candidate)
        try:
            metadata = huashu_metadata(entry)
        except (OSError, ValueError):
            continue
        if metadata["version"] == expected:
            return metadata["entry"]
    raise FileNotFoundError(
        "Installed huashu-chrome %s not found. Pass --huashu-entry PATH or set "
        "HUASHU_CHROME_ENTRY; set HUASHU_CHROME_VERSION only after checking a "
        "different installed version. No auto-install is performed." % expected
    )


def dsh_version(dsh_cli):
    try:
        result = subprocess.run(
            [str(dsh_cli), "--version"], capture_output=True, text=True, timeout=5,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout).strip()[:500]}
    return {"ok": True, "version": result.stdout.strip()}


def preflight(dsh=None, huashu_entry=None):
    """Offline diagnostics: no DSH model session, model call, or result write."""
    info = platform_info()
    report = {
        "preflight": "passed" if info["platform_supported"] else "unsupported_platform",
        "model_called": False,
        "skill_root": str(Path(__file__).resolve().parents[1]),
        "config": str(Path(__file__).resolve().parents[1] / "config/max.yml"),
        **info,
        "model": {"provider": "deepseek-official", "model": "deepseek-flash", "reasoning": "max"},
    }
    if not info["platform_supported"]:
        return report
    errors = []
    try:
        resolved = resolve_dsh(dsh)
        report["dsh"] = resolved
        version = dsh_version(resolved)
        report["dsh_version"] = version
        if not version.get("ok") or not str(version.get("version") or "").strip():
            errors.append("dsh version: " + str(version.get("error") or "empty --version output"))
        report["runtime_root"] = runtime_root_for(resolved)
    except (OSError, ValueError) as exc:
        report["dsh"] = None
        report["dsh_error"] = str(exc)
        errors.append("dsh: " + str(exc))
    browser_configured = bool(
        huashu_entry is not None
        or os.environ.get("HUASHU_CHROME_ENTRY")
        or os.environ.get("HUASHU_CHROME_VERSION")
    )
    try:
        report["node"] = resolve_node()
    except (OSError, ValueError) as exc:
        report["node"] = None
        report["node_error"] = str(exc)
        if os.environ.get("NODE_BIN") or browser_configured:
            errors.append("node: " + str(exc))
    if browser_configured:
        try:
            entry = resolve_huashu(huashu_entry)
            report["huashu"] = huashu_metadata(entry)
        except (OSError, ValueError) as exc:
            report["huashu"] = None
            report["huashu_error"] = str(exc)
            errors.append("huashu: " + str(exc))
    if errors:
        report["preflight"] = "failed"
        report["config_errors"] = errors
    return report
