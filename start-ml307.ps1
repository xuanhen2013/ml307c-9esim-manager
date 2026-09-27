param(
    [string]$Python = "python",
    [string]$SerialPort = "COM4",
    [int]$ListenPort = 8080,
    [string]$DataDirectory = (Join-Path $PSScriptRoot ".localdata")
)
$ErrorActionPreference = "Stop"
$runtime = Join-Path $DataDirectory "venv\Scripts\python.exe"
New-Item -ItemType Directory -Force -Path $DataDirectory | Out-Null
if (-not (Test-Path -LiteralPath $runtime)) {
    & $Python -m venv (Join-Path $DataDirectory "venv")
    if ($LASTEXITCODE -ne 0) { throw "Python virtual environment setup failed" }
}
$requirements = Join-Path $PSScriptRoot "requirements-ml307.txt"
$stamp = Join-Path $DataDirectory "requirements.sha256"
$sha = [System.Security.Cryptography.SHA256]::Create()
try { $hash = [BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($requirements))).Replace("-", "") }
finally { $sha.Dispose() }
if (-not (Test-Path -LiteralPath $stamp) -or (Get-Content -LiteralPath $stamp -Raw).Trim() -ne $hash) {
    & $runtime -m pip install --disable-pip-version-check -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
    Set-Content -LiteralPath $stamp -Value $hash
}
$static = Join-Path $PSScriptRoot "frontend\dist"
if (-not (Test-Path -LiteralPath (Join-Path $static "index.html"))) {
    Push-Location (Join-Path $PSScriptRoot "frontend")
    try {
        & npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed" }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
    } finally { Pop-Location }
}
$env:MODEM_BACKEND = "ml307"
$env:ML307_PORT = $SerialPort
$env:ML307_DATA_DIR = $DataDirectory
$env:SMS_FORWARDER_CONFIG = Join-Path $DataDirectory "notifications.conf"
$env:ESIM_FORWARDER_CONFIG = Join-Path $DataDirectory "app.conf"
$env:FOURG_WIFI_ADMIN_HOST = "127.0.0.1"
$env:FOURG_WIFI_ADMIN_PORT = "$ListenPort"
$env:FOURG_WIFI_ADMIN_STATIC_DIR = $static
$env:PYTHONUTF8 = "1"
Write-Host "eSIM-SMS-Forwarder: http://127.0.0.1:$ListenPort (device $SerialPort)"
& $runtime -B -u (Join-Path $PSScriptRoot "deploy\web_admin\4g_wifi_admin.py")
exit $LASTEXITCODE
