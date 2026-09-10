# Build the extension zip and (optionally) install it into a Blender build.
#
#   .\build.ps1                 -> builds dist\ladybug_tools-<ver>.zip
#   .\build.ps1 -Install        -> builds and installs into the Blender below
#   .\build.ps1 -Blender "C:\path\to\blender.exe" -Install
param(
    [string]$Blender = "C:\SOFTWARES\Blender Builds\stable\blender-5.2.1-lts.9e2066aef7ef\blender.exe",
    [switch]$Install
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $root "ladybug_tools"
$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force $dist | Out-Null
Get-ChildItem $src -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

& $Blender --command extension validate $src
& $Blender --command extension build --source-dir $src --output-dir $dist
$zip = Get-ChildItem $dist -Filter "ladybug_tools-*.zip" | Sort-Object LastWriteTime | Select-Object -Last 1
Write-Host "Built: $($zip.FullName)"

if ($Install) {
    & $Blender --command extension install-file -r user_default -e $zip.FullName
    Write-Host "Installed into user_default repository."
}
