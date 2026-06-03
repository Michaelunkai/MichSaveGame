$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $Root 'scripts\run-MichSaveGame.ps1') @args
exit $LASTEXITCODE
