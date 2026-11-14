# videocli installer for Windows (PowerShell 5.1+).
#
#   irm https://<project-domain>/install.ps1 | iex
#   & ([scriptblock]::Create((irm https://<project-domain>/install.ps1))) -Profile finishing
#
# Steps: ensure uv, `uv tool install videocli`, then `videocli setup --profile NAME`.
# Safe to re-run: it upgrades in place. Set $env:VIDEOCLI_INSTALL_SOURCE to install from git.
param(
  [ValidateSet("", "lecture", "finishing", "artistic", "full")]
  [string]$Profile = ""
)
$ErrorActionPreference = "Stop"

function Say($msg) { Write-Host "videocli-install: $msg" }

$source = if ($env:VIDEOCLI_INSTALL_SOURCE) { $env:VIDEOCLI_INSTALL_SOURCE } else { "videocli" }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Say "installing uv"
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Say "installing $source"
uv tool install --upgrade --python ">=3.10" $source
uv tool update-shell | Out-Null

$interactive = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected
if ($Profile -ne "") {
  videocli setup --profile $Profile
} elseif ($interactive) {
  videocli setup
} else {
  Say "no console attached: installing the 'lecture' profile"
  videocli setup --profile lecture --json | Out-Null
  Say "add more later with: videocli setup --profile finishing   (or --with GROUP)"
}
Say "done. Check with: videocli doctor"
