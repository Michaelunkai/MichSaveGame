$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
Set-Location -LiteralPath $Root

$PythonCommand = $null
$PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($PyLauncher) {
    $PythonCommand = @($PyLauncher.Source, '-3')
} else {
    $Candidates = @(Get-Command python.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -and ($_.Source -notmatch '\\WindowsApps\\') })
    if ($Candidates.Count -gt 0) {
        $PythonCommand = @($Candidates[0].Source)
    }
}

if (-not $PythonCommand) {
    Write-Error 'No real Python installation found. Install Python 3 from python.org, or install it with: winget install Python.Python.3.12'
    exit 1
}

if ($args.Count -eq 0) { $args = @('gui') }
$PythonExtraArgs = @()
if ($PythonCommand.Count -gt 1) {
    $PythonExtraArgs = @($PythonCommand[1..($PythonCommand.Count - 1)])
}
& $PythonCommand[0] @PythonExtraArgs "$Root\michsavegame.py" @args
$Code = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
exit $Code
