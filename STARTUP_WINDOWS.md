# Windows one-command startup

Run from the folder containing `app.py`:

```powershell
python app.py
```

`app.py` is now only the bootstrap/launcher. The Flask application lives in
`server.py`. The launcher:

1. Creates `.venv` when missing.
2. Installs/updates `requirements.txt` only when its SHA-256 changes.
3. Runs the pywin32 post-install step when available.
4. Starts `server.py` directly with `.venv\Scripts\python.exe`.
5. Waits for the Flask process and returns its exit code.

It does **not** recursively execute `app.py`, avoiding the Windows duplicated
path and immediate-return problems from earlier versions.

No PowerShell `activate` command is required.

For a Windows Server deployment, run:

```powershell
cd C:\PDL_Email_Intelligence
python app.py
```

If startup fails, the actual `server.py` traceback remains visible in the
same PowerShell window.
