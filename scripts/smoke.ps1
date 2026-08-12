[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$smokeRoot = Join-Path $temporaryRoot ('cso-smoke-' + [guid]::NewGuid().ToString('N'))
$resolvedSmokeRoot = [IO.Path]::GetFullPath($smokeRoot)

if (-not $resolvedSmokeRoot.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to use a smoke directory outside the system temporary directory.'
}

$stateRoot = Join-Path $smokeRoot 'state'
$skillsRoot = Join-Path $smokeRoot 'skills'
$previousBytecodeSetting = $env:PYTHONDONTWRITEBYTECODE
$env:PYTHONDONTWRITEBYTECODE = '1'

Push-Location -LiteralPath $projectRoot
try {
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed.' }

    & '.\installer\install.ps1' -Action Install -Profile Universal -InstallRoot $stateRoot -SkillsDir $skillsRoot -DryRun -Json
    if ($LASTEXITCODE -ne 0) { throw 'PowerShell installer dry-run failed.' }
    if (Test-Path -LiteralPath $smokeRoot) { throw 'Dry-run created persistent files.' }

    & '.\installer\install.ps1' -Action Install -Profile Universal -InstallRoot $stateRoot -SkillsDir $skillsRoot -Json
    if ($LASTEXITCODE -ne 0) { throw 'PowerShell install failed.' }
    & '.\installer\install.ps1' -Action Audit -InstallRoot $stateRoot -SkillsDir $skillsRoot -Json
    if ($LASTEXITCODE -ne 0) { throw 'PowerShell audit failed.' }
    & '.\installer\install.ps1' -Action Activate -Profile Economy -InstallRoot $stateRoot -SkillsDir $skillsRoot -Json
    if ($LASTEXITCODE -ne 0) { throw 'PowerShell activation failed.' }
    & '.\installer\install.ps1' -Action Rollback -InstallRoot $stateRoot -SkillsDir $skillsRoot -Json
    if ($LASTEXITCODE -ne 0) { throw 'PowerShell rollback failed.' }

    Write-Host 'PowerShell smoke test passed.'
} finally {
    Pop-Location
    $env:PYTHONDONTWRITEBYTECODE = $previousBytecodeSetting
    if (Test-Path -LiteralPath $resolvedSmokeRoot) {
        $verified = [IO.Path]::GetFullPath($resolvedSmokeRoot)
        if (-not $verified.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase) -or
            -not ([IO.Path]::GetFileName($verified)).StartsWith('cso-smoke-', [StringComparison]::Ordinal)) {
            throw 'Refusing unsafe smoke cleanup.'
        }
        Remove-Item -LiteralPath $verified -Recurse -Force
    }
}
