[CmdletBinding()]
param(
    [ValidateSet('Install', 'Activate', 'Audit', 'Rollback', 'Plan', 'Profiles', 'Route')]
    [string]$Action = 'Install',
    [string]$Profile = 'Universal',
    [string]$InstallRoot,
    [string]$SkillsDir,
    [switch]$DryRun,
    [switch]$Json,
    [string]$Task
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'python-discovery.ps1')
$python = Find-CsoPython
$pythonExecutable = $python.Executable
$pythonPrefix = @($python.Prefix)

$command = $Action.ToLowerInvariant()
$arguments = @('-m', 'skill_orchestrator', $command)
if ($command -in @('install', 'activate', 'plan', 'route')) {
    $arguments += @('--profile', $Profile)
}
if ($command -in @('install', 'activate', 'audit', 'rollback', 'plan')) {
    if ($PSBoundParameters.ContainsKey('InstallRoot')) {
        $arguments += @('--install-root', $InstallRoot)
    }
    if ($PSBoundParameters.ContainsKey('SkillsDir')) {
        $arguments += @('--skills-dir', $SkillsDir)
    }
}
if ($DryRun) {
    if ($command -notin @('install', 'activate', 'rollback')) {
        throw '-DryRun is supported only for Install, Activate, and Rollback.'
    }
    $arguments += '--dry-run'
}
if ($command -eq 'route') {
    if (-not $PSBoundParameters.ContainsKey('Task')) {
        throw '-Task is required for Route.'
    }
    $arguments += @('--task', $Task)
}
if ($Json) {
    $arguments += '--json'
}

$previousBytecodeSetting = $env:PYTHONDONTWRITEBYTECODE
$env:PYTHONDONTWRITEBYTECODE = '1'
Push-Location -LiteralPath $projectRoot
try {
    & $pythonExecutable @pythonPrefix @arguments
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
    $env:PYTHONDONTWRITEBYTECODE = $previousBytecodeSetting
}
exit $exitCode
