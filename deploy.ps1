# One-command deploy from PowerShell (Windows). Same flow as deploy.sh:
# pack source tree -> scp to the Pi -> on-pi-install.sh (as root) swaps the
# tree in and restarts the systemd service.
#
#   .\deploy.ps1                # default host: urad-pi
#   .\deploy.ps1 some-other-pi
#
# Override the recording server used on first run (when
# /etc/default/lares-setup-tool is created) via env vars:
#   $env:LARES_UDP_HOST = "192.168.0.192"; .\deploy.ps1
param(
    [string]$Target = "urad-pi"
)
$ErrorActionPreference = "Stop"
$dest = "/opt/lares-sense"
$udpHost = if ($env:LARES_UDP_HOST) { $env:LARES_UDP_HOST } else { "192.168.0.192" }
$udpPort = if ($env:LARES_UDP_PORT) { $env:LARES_UDP_PORT } else { "5005" }
$tgz = Join-Path $env:TEMP "lares-deploy.tgz"

function Assert-LastExit([string]$msg) {
    if ($LASTEXITCODE -ne 0) { throw "$msg (exit $LASTEXITCODE)" }
}

Push-Location $PSScriptRoot
try {
    Write-Host "==> [1/4] Packing source tree" -ForegroundColor Cyan
    if (Test-Path $tgz) { Remove-Item $tgz -Force }
    & tar -czf $tgz `
        --exclude=.git --exclude=.venv --exclude=.pytest_cache `
        --exclude=__pycache__ --exclude=*.pyc `
        --exclude=docs_and_tools --exclude=.env .
    Assert-LastExit "tar failed"

    Write-Host "==> [2/4] Uploading to $Target" -ForegroundColor Cyan
    & scp -q $tgz "${Target}:/tmp/lares-deploy.tgz"
    Assert-LastExit "scp failed"
    Remove-Item $tgz -Force -ErrorAction SilentlyContinue

    Write-Host "==> [3/4] Preparing target dir (sudo prompt only on first run)" -ForegroundColor Cyan
    & ssh -t $Target "test -d $dest && test -O $dest || sudo mkdir -p $dest && sudo chown darky:darky $dest"
    Assert-LastExit "ssh prepare failed"
    & ssh $Target "mkdir -p $dest/incoming && tar -xzf /tmp/lares-deploy.tgz -C $dest/incoming && rm /tmp/lares-deploy.tgz"
    Assert-LastExit "remote extract failed"

    Write-Host "==> [4/4] Install + restart on the Pi (sudo password may be asked)" -ForegroundColor Cyan
    & ssh -t $Target "sudo bash $dest/incoming/deploy/on-pi-install.sh '$udpHost' '$udpPort'"
    Assert-LastExit "install failed"

    Write-Host "==> Done. Setup Tool: http://$Target.local:8000/" -ForegroundColor Green
}
finally { Pop-Location }
