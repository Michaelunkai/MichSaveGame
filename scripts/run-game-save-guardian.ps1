$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$Py = "python"
Set-Location $Root
& $Py "$Root\game-save-guardian.py" @args
