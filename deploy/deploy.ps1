<#
Deploy FastImpact Preach to a Linux server (Infomaniak Cloud VPS / Public Cloud) over SSH.

  .\deploy\deploy.ps1 -Server 83.166.x.x -User debian -Key C:\path\to\key -Domain shorts.example.ch

Before the first run:
  * the domain's DNS A record must point to the server's IP;
  * ports 80 and 443 must be open in the Infomaniak firewall (Manager → VPS → Firewall).
The private key is only passed to ssh/scp; it is never copied into the project or the server.
#>
param(
    [Parameter(Mandatory)] [string] $Server,
    [Parameter(Mandatory)] [string] $Domain,
    [string] $User = "debian",
    [Parameter(Mandatory)] [string] $Key
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# Windows OpenSSH ignores private keys that other accounts can read: restrict it to the current user.
icacls $Key /inheritance:r /grant:r "${env:USERNAME}:(R)" | Out-Null
if ($LASTEXITCODE) { throw "Could not restrict permissions on $Key" }
$bundle = Join-Path $env:TEMP "fastimpact-deploy.tgz"
$sshOpts = @("-i", $Key, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15")
$target = "$User@$Server"

Write-Host "==> Packing application"
Push-Location $root
try {
    # Windows' own tar (bsdtar): Git's GNU tar would read "C:\..." as a remote host
    & (Join-Path $env:SystemRoot "System32\tar.exe") -czf $bundle --exclude="__pycache__" Dockerfile docker-compose.yml .dockerignore deploy fip app static assets/fonts assets/brand
} finally { Pop-Location }

Write-Host "==> Uploading to $target"
ssh @sshOpts $target "mkdir -p ~/fastimpact"
if ($LASTEXITCODE) { throw "SSH connection failed" }
scp @sshOpts $bundle "${target}:fastimpact/bundle.tgz"
if ($LASTEXITCODE) { throw "Upload failed" }

Write-Host "==> Installing / updating on the server"
ssh @sshOpts $target "cd ~/fastimpact && tar -xzf bundle.tgz && rm bundle.tgz && bash deploy/server-setup.sh '$Domain'"
if ($LASTEXITCODE) { throw "Server setup failed" }
Remove-Item $bundle -ErrorAction SilentlyContinue
Write-Host "`nDeployed: https://$Domain"
