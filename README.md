# MichSaveGame

MichSaveGame is a premium local Windows game-save manager. It discovers save data across the PC, creates verifiable backups, previews/restores backups safely, and now includes a C-drive leftover cleaner for any game name you choose.

The app is designed for this default backup location:

`F:\backup\gamesaves`

Each backup gets its own timestamped folder. Every cleanup delete is preview-first and copies files into a quarantine folder before removing the original leftovers.

## What it does

- Discovers game saves from every currently visible Windows filesystem drive, all Windows user profiles on those drives, Documents/My Games, Saved Games, AppData, Public Documents, ProgramData, Steam userdata/compatdata, Steam/RUNE emulator hints, Epic manifests, installed game folders, and a deep all-drive fallback for games that save outside known locations.
- Shows game title, confidence, exact Windows path, file count, size, latest write time, and discovery reason.
- Backs up selected games with a manifest and archive.
- Verifies backups and detects missing payload files.
- Restores backups to original paths or an alternate root for another machine.
- Finds C-drive leftovers for a specific game name under common cleanup roots.
- Deletes leftovers safely by quarantining the folder into `F:\backup\gamesaves\_cleanup-quarantine` first.
- Runs both as CLI and as a beautiful browser-first local dashboard.

## Run the app

From PowerShell:

```powershell
& 'F:\study\Windows\Applications\Gaming\SaveData\Backup\Tools\Python\MichSaveGame\run-MichSaveGame.ps1'
```

The runner now defaults to `gui`, so no argument is required. If port `8765` is blocked or already used by Windows, MichSaveGame automatically picks the next safe local port and opens that URL. Discovery has a fast known-save-root pass plus a deep every-visible-drive fallback so non-standard save folders on C:, D:, E:, F:, USB, removable, or other mounted drives are still included.

The local dashboard opens at:

`http://127.0.0.1:8765/app`

## CLI examples

Discover one game:

```powershell
.\scripts\run-MichSaveGame.ps1 discover --game "Edge of Eternity"
```

Discover all visible save groups:

```powershell
.\scripts\run-MichSaveGame.ps1 discover --all --refresh
```

Back up a game:

```powershell
.\scripts\run-MichSaveGame.ps1 backup --game "Edge of Eternity"
```

List backups:

```powershell
.\scripts\run-MichSaveGame.ps1 list-backups
```

Preview restore:

```powershell
.\scripts\run-MichSaveGame.ps1 restore "F:\backup\gamesaves\Edge-of-Eternity-YYYYMMDD-HHMMSS" --dry-run
```

Restore to another machine/root preview folder:

```powershell
.\scripts\run-MichSaveGame.ps1 restore "F:\backup\gamesaves\Edge-of-Eternity-YYYYMMDD-HHMMSS" --target-root "D:\RestorePreview"
```

Preview C-drive leftovers for a game:

```powershell
.\scripts\run-MichSaveGame.ps1 cleanup --game "Edge of Eternity"
```

Delete C-drive leftovers safely after preview:

```powershell
.\scripts\run-MichSaveGame.ps1 cleanup --game "Edge of Eternity" --execute
```

## Safety model

- Cleanup is preview-first unless `--execute` is used or the dashboard confirmation is accepted.
- The app recomputes cleanup candidates on the server side. It does not trust browser-supplied file paths.
- Local mutating API calls require a per-launch `X-UGSG-Token` and origin checks.
- HTML/API-derived strings are escaped in the dashboard before rendering.
- Deleted leftover folders are copied into quarantine before removal.
- Restore can run as dry-run or into an alternate root before touching original paths.

## Important files

- `michsavegame.py` — primary Python entry point.
- `game-save-guardian.py` — compatibility entry point.
- `gamesave_guardian/app.py` — discovery, backup, restore, cleanup, CLI, API, and web dashboard.
- `scripts/run-MichSaveGame.ps1` — PowerShell launcher.
- `scripts/run-game-save-guardian.ps1` — compatibility launcher.
- `tests/` — regression tests for discovery, backup/restore/verify, API safety, dashboard shell, and C-drive cleanup.

## Test locally

```powershell
python -m py_compile michsavegame.py game-save-guardian.py gamesave_guardian\app.py
python -m pytest -q
```

On WSL-mounted drives, use a temporary pytest cache if needed:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -o cache_dir=/tmp/michsavegame-pytest-cache
```

## Troubleshooting

- If Python is missing, install Python 3 from python.org or run `winget install Python.Python.3.12`.
- If the browser does not open automatically, visit `http://127.0.0.1:8765/app` manually after launching.
- If a game is not found, use the search box with the exact title/folder name and run a refreshed discovery. MichSaveGame scans every visible filesystem drive it can access; folders blocked by Windows permissions or offline/unmounted drives cannot be read until Windows exposes them.
- If a cleanup candidate looks wrong, do not delete it. The exact path and reason are shown before any action.
- Always run restore preview before restoring over live save folders.
