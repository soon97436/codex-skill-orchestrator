function Find-CsoPython {
    $candidates = @(
        @{ Name = 'py'; Prefix = @('-3') },
        @{ Name = 'python3'; Prefix = @() },
        @{ Name = 'python'; Prefix = @() }
    )
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.Name -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $command) { continue }
        & $command.Source @($candidate.Prefix) -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{
                Executable = $command.Source
                Prefix = @($candidate.Prefix)
            }
        }
    }
    throw 'Python 3.9 or newer is required.'
}
