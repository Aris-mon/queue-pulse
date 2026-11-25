"""
PDL Email Intelligence - Windows one-command launcher.

Run:
    python app.py

This launcher creates/updates .venv and then starts server.py with the
virtual-environment interpreter. It deliberately does NOT recursively execute
app.py, which avoids Windows path/relaunch issues.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENV_DIR = BASE_DIR / ".venv"
REQ_FILE = BASE_DIR / "requirements.txt"
SERVER_FILE = BASE_DIR / "server.py"


def log(message):
    print(f"[PDL] {message}", flush=True)


def venv_python():
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def create_venv(python_executable):
    log(f"Creating virtual environment: {VENV_DIR}")
    builder = venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False)
    builder.create(VENV_DIR)
    if not python_executable().exists():
        raise RuntimeError("Virtual environment was created but its Python executable is missing.")


def update_requirements(python_executable):
    if not REQ_FILE.exists():
        return

    digest = hashlib.sha256(REQ_FILE.read_bytes()).hexdigest()
    stamp = VENV_DIR / ".pdl_requirements.sha256"
    old = stamp.read_text(encoding="utf-8").strip() if stamp.exists() else ""

    if digest == old:
        log("Python environment is up to date.")
        return

    log("Installing/updating Python dependencies...")
    subprocess.check_call([
        str(python_executable), "-m", "pip", "install",
        "--disable-pip-version-check", "-r", str(REQ_FILE)
    ])
    stamp.write_text(digest, encoding="utf-8")
    log("Python dependencies are ready.")


def ensure_pywin32(python_executable):
    """No-op for Graph mailbox deployments; Outlook/COM is not required."""
    return


def main():
    if not SERVER_FILE.exists():
        raise FileNotFoundError(f"Missing application entry point: {SERVER_FILE}")

    # If someone starts app.py with a venv interpreter, don't create another
    # environment or launch a second copy.
    current_python = Path(sys.executable).resolve()
    target = venv_python().resolve()

    if current_python == target:
        log(f"Running inside virtual environment: {current_python}")
        return run_server(current_python)

    if not target.exists():
        create_venv(venv_python)

    update_requirements(target)

    log(f"Starting Flask application with: {target}")
    log(f"Application entry point: {SERVER_FILE}")

    env = os.environ.copy()
    env["PDL_BASE_DIR"] = str(BASE_DIR)
    # The in-app updater uses this PID so it can wait for the launcher to
    # terminate before restarting the upgraded application.
    env["PDL_LAUNCHER_PID"] = str(os.getpid())
    env["PYTHONUNBUFFERED"] = "1"

    # Start server.py directly. Do NOT recursively invoke app.py.
    result = subprocess.run(
        [str(target), str(SERVER_FILE), *sys.argv[1:]],
        cwd=str(BASE_DIR),
        env=env,
    )
    raise SystemExit(result.returncode)


def run_server(python_executable):
    # This branch is only reached if app.py itself is invoked by the venv.
    # server.py is still the real application entry point.
    result = subprocess.run(
        [str(python_executable), str(SERVER_FILE), *sys.argv[1:]],
        cwd=str(BASE_DIR),
        env=os.environ.copy(),
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Application stopped.")
    except subprocess.CalledProcessError as exc:
        log(f"Startup failed. Command returned exit code {exc.returncode}.")
        raise
    except Exception as exc:
        log(f"Startup failed: {exc}")
        raise
