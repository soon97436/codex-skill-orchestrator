[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'cso.py'
. (Join-Path $PSScriptRoot 'python-discovery.ps1')
$python = Find-CsoPython
$pythonExecutable = $python.Executable
$pythonPrefix = @($python.Prefix)

$previousBytecodeSetting = $env:PYTHONDONTWRITEBYTECODE
$env:PYTHONDONTWRITEBYTECODE = '1'
try {
    & $pythonExecutable @pythonPrefix $launcher @Arguments
    exit $LASTEXITCODE
} finally {
    $env:PYTHONDONTWRITEBYTECODE = $previousBytecodeSetting
}
