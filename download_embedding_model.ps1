param(
    [string]$ModelId = "sentence-transformers/all-MiniLM-L6-v2",
    [string]$Target = "models/all-MiniLM-L6-v2"
)

Write-Host "This is an EXPLICIT one-time model staging command." -ForegroundColor Yellow
Write-Host "It downloads only the embedding model; it does NOT read or upload application emails." -ForegroundColor Yellow
Write-Host "Run this only on a machine/network approved by your security team." -ForegroundColor Yellow

python -c "from sentence_transformers import SentenceTransformer; import os; SentenceTransformer(r'$ModelId').save(r'$Target')"
if ($LASTEXITCODE -ne 0) { throw "Embedding model download failed." }
Write-Host "Model staged at $Target" -ForegroundColor Green
