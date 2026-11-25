"""
Detached Windows-safe release updater for PDL Email Intelligence.

This helper is launched by server.py before the Flask process exits. It waits
for the running server and its app.py launcher, backs up the current code,
replaces only release/application files, preserves instance/.env/.venv, and
starts app.py again.

Usage is internal; administrators trigger it from the Administration page.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


EXCLUDED_DIRS = {
    ".venv", ".git", "__pycache__", ".pytest_cache",
    "instance", "node_modules",
}
EXCLUDED_FILES = {
    ".env",
}
STATUS_NAME = "update_status.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def write_status(base, payload):
    root = base / "instance" / "updates"
    root.mkdir(parents=True, exist_ok=True)
    path = root / STATUS_NAME
    tmp = root / (STATUS_NAME + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def process_exists(pid):
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return str(pid) in result.stdout
        except Exception:
            pass
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def wait_for_exit(pid, timeout=180):
    if not pid:
        return True
    deadline = time.time() + timeout
    while process_exists(pid) and time.time() < deadline:
        time.sleep(1)
    return not process_exists(pid)


def copy_tree(src, dst, *, skip_dirs=None):
    skip_dirs = set(skip_dirs or ())
    for current, dirs, files in os.walk(src):
        current_path = Path(current)
        rel = current_path.relative_to(src)
        if any(part in skip_dirs for part in rel.parts):
            dirs[:] = []
            continue

        # Prevent walking excluded runtime directories.
        dirs[:] = [d for d in dirs if d not in skip_dirs]

        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)

        for name in files:
            if name in EXCLUDED_FILES:
                continue
            source = current_path / name
            target = target_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def backup_current(base, backup_dir):
    backup_dir.mkdir(parents=True, exist_ok=True)
    copy_tree(base, backup_dir, skip_dirs=EXCLUDED_DIRS | {"instance"})


def replace_release(base, release):
    # Release files are copied over the existing application. Runtime state is
    # deliberately excluded so the database, Graph secret key/config, .env and
    # virtual environment survive the upgrade.
    copy_tree(release, base, skip_dirs=EXCLUDED_DIRS | {"instance"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--stage-parent", required=True)
    parser.add_argument("--server-pid", type=int, required=True)
    parser.add_argument("--launcher-pid", type=int, default=0)
    parser.add_argument("--current-version", default="")
    parser.add_argument("--target-version", required=True)
    args = parser.parse_args()

    base = Path(__file__).resolve().parent
    release = Path(args.stage).resolve()
    stage_parent = Path(args.stage_parent).resolve()
    update_root = base / "instance" / "updates"
    backup = update_root / "backups" / f"{args.current_version or 'unknown'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    status = {
        "status": "waiting",
        "current_version": args.current_version,
        "target_version": args.target_version,
        "started_at": now(),
        "message": "Waiting for the running application to stop safely.",
    }
    write_status(base, status)

    try:
        if not (release / "server.py").is_file() or not (release / "VERSION.txt").is_file():
            raise RuntimeError("Staged release is invalid: server.py/VERSION.txt missing.")

        if not wait_for_exit(args.server_pid):
            raise RuntimeError("Timed out waiting for the current application process to stop.")

        # app.py normally exits immediately after server.py stops. Give it a
        # short grace period, then continue; if the launcher is still present
        # it will exit as soon as its child server process has returned.
        if args.launcher_pid:
            if not wait_for_exit(args.launcher_pid, timeout=60):
                raise RuntimeError("Timed out waiting for the application launcher to stop.")

        status.update({
            "status": "backing_up",
            "message": "Creating a backup of the current application code.",
        })
        write_status(base, status)
        backup_current(base, backup)

        status.update({
            "status": "installing",
            "message": f"Installing release {args.target_version}. Runtime data is being preserved.",
            "backup": str(backup),
        })
        write_status(base, status)
        replace_release(base, release)

        # Verify the new application entry point before attempting restart.
        if not (base / "app.py").is_file() or not (base / "server.py").is_file():
            raise RuntimeError("Release installation verification failed: app.py/server.py missing.")

        status.update({
            "status": "restarting",
            "message": f"Release {args.target_version} installed. Starting the application.",
        })
        write_status(base, status)

        env = os.environ.copy()
        env["PDL_BASE_DIR"] = str(base)
        env["PDL_UPGRADE_TARGET"] = args.target_version
        env["PYTHONUNBUFFERED"] = "1"

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        subprocess.Popen(
            [sys.executable, str(base / "app.py")],
            cwd=str(base),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )

        status.update({
            "status": "completed",
            "finished_at": now(),
            "message": f"Release {args.target_version} applied. The application was restarted.",
        })
        write_status(base, status)

        # Remove only the temporary extracted package. Keep backups for rollback.
        try:
            shutil.rmtree(stage_parent, ignore_errors=True)
        except Exception:
            pass

    except Exception as exc:
        status.update({
            "status": "failed",
            "finished_at": now(),
            "message": str(exc)[:2000],
            "backup": str(backup) if backup.exists() else None,
        })
        write_status(base, status)

        # Do not leave a half-staged update behind.
        try:
            shutil.rmtree(stage_parent, ignore_errors=True)
        except Exception:
            pass

        # If replacement failed before restart, try to bring the existing app
        # back. The caller can inspect update_status.json for the exact error.
        try:
            subprocess.Popen(
                [sys.executable, str(base / "app.py")],
                cwd=str(base),
                env=os.environ.copy(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()
