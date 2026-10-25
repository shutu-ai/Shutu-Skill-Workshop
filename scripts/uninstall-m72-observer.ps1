[CmdletBinding()]
param(
    [ValidateSet('User', 'Project')]
    [string]$Scope = 'User',
    [string]$CodexHome = '',
    [switch]$WhatIf
)

$args = @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'install-m72-observer.ps1'), '-Mode', 'Disable', '-Scope', $Scope)
if ($CodexHome) { $args += @('-CodexHome', $CodexHome) }
if ($WhatIf) { $args += '-WhatIf' }
& powershell @args
exit $LASTEXITCODE
