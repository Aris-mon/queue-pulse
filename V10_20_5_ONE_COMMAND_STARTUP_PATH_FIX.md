# V10.20.5 — One-Command Startup Path Fix

Fixed the virtual-environment relaunch logic. `app.py` now passes its absolute path when restarting under `.venv`, preventing duplicated paths such as `...\PDL\app.py\PDL\app.py` and the resulting `[Errno 2] No such file or directory` error.

Run only:

```powershell
python app.py
```

The application creates/updates `.venv` as required and launches itself using `.venv\Scripts\python.exe`.
