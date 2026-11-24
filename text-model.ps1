# Text-only profile for the verified RTX 2060 6GB configuration.
param([string]$Ollama=(Join-Path $env:LOCALAPPDATA 'Programs/Ollama/ollama.exe'))
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$source=Invoke-RestMethod -Method Post -Uri http://127.0.0.1:11434/api/show -ContentType application/json -Body '{"model":"qwen3.8:27b"}'
$manifest=Join-Path $env:USERPROFILE '.ollama/models/manifests/registry.ollama.ai/library/qwen3.8/27b'
if ($env:OLLAMA_MODELS) { $manifest=Join-Path $env:OLLAMA_MODELS 'manifests/registry.ollama.ai/library/qwen3.8/27b' }
$layers=(Get-Content -Raw -LiteralPath $manifest | ConvertFrom-Json).layers
$vision=@($layers | Where-Object mediaType -eq 'application/vnd.ollama.image.projector' | ForEach-Object { $_.digest.Replace(':','-') })
$lines=foreach ($line in ($source.modelfile -split "\r?\n")) {
    if ($line.StartsWith('FROM ') -and ($vision | Where-Object {$line.Contains($_)})) { continue }
    if ($line.StartsWith('PARAMETER draft_num_predict ')) { 'PARAMETER draft_num_predict 0' } else { $line }
}
$modelFile=Join-Path $PSScriptRoot '.runtime/Modelfile.text'
[IO.File]::WriteAllLines($modelFile,@($lines)+@('PARAMETER num_gpu 17','PARAMETER num_batch 96'))
& $Ollama create clicker-qwen3.8:27b -f $modelFile
if ($LASTEXITCODE) { throw 'Could not create the local 27B text profile.' }

