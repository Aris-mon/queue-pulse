$ErrorActionPreference = "Stop"
if (!(Test-Path ".venv")) {
  py -3.11 -m venv .venv
}
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
if (!(Test-Path ".env")) {
  Copy-Item .env.example .env
  Write-Host "Created .env. Edit it before starting."
}
python app.py
