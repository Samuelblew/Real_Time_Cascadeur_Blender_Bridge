[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CascadeurPath
)

$ErrorActionPreference = 'Stop'
$installationRoot = (Resolve-Path -LiteralPath $CascadeurPath).Path
$scriptsRoot = Join-Path $installationRoot 'resources\scripts\python\scripts'
if (-not (Test-Path -LiteralPath $scriptsRoot -PathType Container)) {
    throw "Pasta de scripts Cascadeur nao encontrada: $scriptsRoot"
}
$sourceRoot = Join-Path $PSScriptRoot 'cascadeur_scripts'
$packageRoot = Join-Path $sourceRoot 'cascadeur_live_link_package'
$requiredFiles = @('cascadeur_streamer.py', 'live_sync.py')
foreach ($name in $requiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot $name) -PathType Leaf)) {
        throw "Arquivo ausente: $name. Baixe o repositorio inteiro, nao somente este instalador."
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $packageRoot 'start_streamer.py') -PathType Leaf)) {
    throw 'Pacote de comandos incompleto: cascadeur_live_link_package/start_streamer.py'
}
$destination = Join-Path $scriptsRoot 'blender_live_link'
if (Test-Path -LiteralPath $destination) {
    $backupRoot = Join-Path $installationRoot 'blender_bridge_backups'
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    $backup = Join-Path $backupRoot ('blender_live_link_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
    Copy-Item -LiteralPath $destination -Destination $backup -Recurse
    Write-Host "Backup anterior: $backup"
}
New-Item -ItemType Directory -Path $destination -Force | Out-Null
Get-ChildItem -LiteralPath $packageRoot -Filter '*.py' -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $destination $_.Name) -Force
}
foreach ($name in $requiredFiles) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $name) -Destination (Join-Path $destination $name) -Force
}
Write-Host "Instalado: $destination"
Write-Host 'Reabra o Cascadeur e execute Scripts > Blender Live Link > Start Streamer.'
Write-Host 'No Blender, instale o ZIP separadamente e clique Start Live Link no painel CEB.'
