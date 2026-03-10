$r = (Invoke-WebRequest -Uri 'https://api.github.com/repos/rhasspy/piper/releases/latest' -UseBasicParsing).Content | ConvertFrom-Json
Write-Host "Tag: $($r.tag_name)"
$r.assets | ForEach-Object { Write-Host $_.name; Write-Host $_.browser_download_url }
