# Download Piper TTS (Windows) and en_US-lessac-medium voice for Reddit Video Builder.
# Run from project root or webapp folder. Saves to clipper\piper\

$ErrorActionPreference = "Stop"
$ProjectRoot = if (Test-Path ".\piper") { (Get-Location).Path } else { (Get-Item $PSScriptRoot).Parent.FullName }
$PiperDir = Join-Path $ProjectRoot "piper"
$PiperZip = Join-Path $PiperDir "piper_windows_amd64.zip"

Write-Host "Piper will be installed to: $PiperDir"
New-Item -ItemType Directory -Force -Path $PiperDir | Out-Null

# Piper Windows binary (GitHub release)
$PiperUrl = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip"
Write-Host "Downloading Piper Windows binary..."
try {
    Invoke-WebRequest -Uri $PiperUrl -OutFile $PiperZip -UseBasicParsing
} catch {
    Write-Host "If download fails, get it manually from: https://github.com/rhasspy/piper/releases"
    throw
}
Expand-Archive -Path $PiperZip -DestinationPath $PiperDir -Force
Remove-Item $PiperZip -Force -ErrorAction SilentlyContinue

# Find piper.exe (might be in a subfolder after extract)
$PiperExe = Get-ChildItem -Path $PiperDir -Filter "piper.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $PiperExe) {
    Write-Host "Extracted but piper.exe not found. Check " $PiperDir
    exit 1
}
$PiperExePath = $PiperExe.FullName
Write-Host "Piper binary: $PiperExePath"

# Voice model: en_US-lessac-medium (Hugging Face)
$VoiceDir = Join-Path $PiperDir "voices"
$VoiceFile = Join-Path $VoiceDir "en_US-lessac-medium.onnx"
$VoiceUrl = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
$VoiceJsonUrl = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"
New-Item -ItemType Directory -Force -Path $VoiceDir | Out-Null
Write-Host "Downloading voice model en_US-lessac-medium..."
try {
    Invoke-WebRequest -Uri $VoiceUrl -OutFile $VoiceFile -UseBasicParsing
    Invoke-WebRequest -Uri $VoiceJsonUrl -OutFile ($VoiceFile + ".json") -UseBasicParsing
} catch {
    Write-Host "Voice download failed. Get it manually from: https://huggingface.co/rhasspy/piper-voices"
    throw
}
Write-Host "Voice model: $VoiceFile"

Write-Host ""
Write-Host "Done. Set these and restart the server:"
Write-Host "  `$env:PIPER_BIN = `"$PiperExePath`""
Write-Host "  `$env:PIPER_MODEL = `"$VoiceFile`""
Write-Host ""
