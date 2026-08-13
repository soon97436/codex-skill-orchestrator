[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$projectFixtureRoot = Join-Path $temporaryRoot ('cso-project-fixture-' + [guid]::NewGuid().ToString('N'))
$installerSmokeRoot = Join-Path $temporaryRoot ('cso-smoke-' + [guid]::NewGuid().ToString('N'))
$temporaryWorkspaces = @(
    [PSCustomObject]@{
        Path = [IO.Path]::GetFullPath($projectFixtureRoot)
        Prefix = 'cso-project-fixture-'
    },
    [PSCustomObject]@{
        Path = [IO.Path]::GetFullPath($installerSmokeRoot)
        Prefix = 'cso-smoke-'
    }
)

foreach ($workspace in $temporaryWorkspaces) {
    if (-not $workspace.Path.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not ([IO.Path]::GetFileName($workspace.Path)).StartsWith($workspace.Prefix, [StringComparison]::Ordinal)) {
        throw 'Refusing to use a smoke directory outside the system temporary directory.'
    }
}

$stateRoot = Join-Path $installerSmokeRoot 'state'
$skillsRoot = Join-Path $installerSmokeRoot 'skills'
$previousBytecodeSetting = $env:PYTHONDONTWRITEBYTECODE
$env:PYTHONDONTWRITEBYTECODE = '1'
. (Join-Path $projectRoot 'installer\python-discovery.ps1')
$python = Find-CsoPython

Push-Location -LiteralPath $projectRoot
try {
    & $python.Executable @($python.Prefix) -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed.' }

    $projectFixture = $projectFixtureRoot
    New-Item -ItemType Directory -Path $projectFixture | Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $projectFixture 'package.json'),
        '{"dependencies":{"react":"1"},"devDependencies":{"vitest":"1"}}' + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    foreach ($arguments in @(
        @('--help'),
        @('analyze', '--help'),
        @('init', '--help'),
        @('doctor', '--help')
    )) {
        & '.\installer\cso.ps1' @arguments | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "CSO help failed: $arguments" }
    }
    & '.\installer\cso.ps1' analyze --project-root $projectFixture | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'CSO analyze failed.' }
    if (Test-Path -LiteralPath (Join-Path $projectFixture '.cso')) { throw 'CSO analyze modified the project.' }
    & '.\installer\cso.ps1' analyze --project-root $projectFixture --json | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'CSO analyze JSON failed.' }
    & '.\installer\cso.ps1' init --project-root $projectFixture --yes | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'CSO init failed.' }
    & '.\installer\cso.ps1' doctor --project-root $projectFixture | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'CSO doctor failed.' }

    if (Test-Path -LiteralPath $installerSmokeRoot) { throw 'Installer smoke root existed before dry-run.' }
    & '.\installer\install.ps1' -Action Install -Profile Universal -InstallRoot $stateRoot -SkillsDir $skillsRoot -DryRun -Json
    if ($LASTEXITCODE -ne 0) { throw 'PowerShell installer dry-run failed.' }
    if (Test-Path -LiteralPath $installerSmokeRoot) { throw 'Dry-run created persistent files.' }

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
    foreach ($workspace in $temporaryWorkspaces) {
        if (Test-Path -LiteralPath $workspace.Path) {
            $verified = [IO.Path]::GetFullPath($workspace.Path)
            if (-not $verified.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase) -or
                -not ([IO.Path]::GetFileName($verified)).StartsWith($workspace.Prefix, [StringComparison]::Ordinal)) {
                throw 'Refusing unsafe smoke cleanup.'
            }
            Remove-Item -LiteralPath $verified -Recurse -Force
        }
    }
}
