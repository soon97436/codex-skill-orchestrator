[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'cso.py'
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

$previousBytecodeSetting = $env:PYTHONDONTWRITEBYTECODE
$env:PYTHONDONTWRITEBYTECODE = '1'
try {
    & $pythonExecutable @pythonPrefix $launcher @Arguments
    exit $LASTEXITCODE
} finally {
    $env:PYTHONDONTWRITEBYTECODE = $previousBytecodeSetting
}
