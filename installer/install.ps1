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
$pythonExecutable = $null
$pythonPrefix = @()

function Test-PythonCandidate {
    param([string]$Executable, [string[]]$Prefix)
    & $Executable @Prefix -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>$null
    return $LASTEXITCODE -eq 0
}

$candidates = @(
    @{ Name = 'py'; Prefix = @('-3') },
    @{ Name = 'python3'; Prefix = @() },
    @{ Name = 'python'; Prefix = @() }
)
foreach ($candidate in $candidates) {
    $command = Get-Command $candidate.Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command -and (Test-PythonCandidate -Executable $command.Source -Prefix $candidate.Prefix)) {
        $pythonExecutable = $command.Source
        $pythonPrefix = $candidate.Prefix
        break
    }
}
if (-not $pythonExecutable) {
    throw 'Python 3.9 or newer is required.'
}

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
