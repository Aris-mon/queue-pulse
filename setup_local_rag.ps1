param(
    [switch]$DownloadEmbeddingModel,
    [switch]$PullQwenModel,
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "PDL Local RAG / LangGraph setup" -ForegroundColor Cyan

if (!(Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating Python 3.11 virtual environment..."
    py -3.11 -m venv .venv
}
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (!$SkipDependencyInstall) {
    Write-Host "Installing application + RAG dependencies..."
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r requirements.txt
}

Write-Host "Checking required Python packages..."
& $Python -c "import flask, flask_login, flask_sqlalchemy, sqlalchemy, apscheduler, requests, cryptography, langgraph, chromadb, sentence_transformers; print('Python dependencies: OK')"

$ModelPath = Join-Path $Root "models\all-MiniLM-L6-v2"
if (!(Test-Path $ModelPath)) {
    if ($DownloadEmbeddingModel) {
        Write-Host "Downloading/staging all-MiniLM-L6-v2..." -ForegroundColor Yellow
        & $Python -c "from sentence_transformers import SentenceTransformer; m=SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); m.save(r'$ModelPath'); print(r'Embedding model staged: $ModelPath')"
    } else {
        Write-Host "Embedding model is NOT staged: $ModelPath" -ForegroundColor Yellow
        Write-Host "On an approved network, rerun: .\setup_local_rag.ps1 -DownloadEmbeddingModel" -ForegroundColor Yellow
    }
} else {
    Write-Host "Embedding model found: $ModelPath" -ForegroundColor Green
}

$Ollama = Get-Command ollama -ErrorAction SilentlyContinue
if ($Ollama) {
    Write-Host "Ollama detected: $($Ollama.Source)" -ForegroundColor Green
    if ($PullQwenModel) {
        Write-Host "Pulling qwen3:8b..." -ForegroundColor Yellow
        & ollama pull qwen3:8b
    }
    & ollama list
} else {
    Write-Host "Ollama CLI not found. Install Ollama before model enrichment can run." -ForegroundColor Yellow
}

Write-Host "Compiling application Python files..."
& $Python -m compileall -q ai server.py db.py app.py

Write-Host "Running local RAG preflight..."
& $Python tools\check_local_rag.py

Write-Host "Setup/preflight completed." -ForegroundColor Green
Write-Host "Start the application with: .\run.ps1"
