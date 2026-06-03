# Universal Game Save Guardian

Universal Game Save Guardian is a Windows-focused game-save discovery, backup, verification, and restore application inspired by GameSave Manager and Ludusavi.

It now provides a polished desktop GUI, a local web dashboard, and a CLI that can scan the whole PC for game save data across visible drives, Windows user profiles, store libraries, emulator/cracked-game save hints, and common save folders.

## What it does

- Finds save data for the currently running game or a named game.
- Runs **Discover Saves** across the whole PC and lists every detected game/save group it can find.
- Shows each save location with the reason it was detected, file count, size, latest modified time, and confidence score.
- Lets you select one or more discovered games in the GUI and back them up.
- Creates every backup in its own timestamped folder under the configured destination.
- Stores `backup_manifest.json` beside copied payload files and also creates a `.tar.gz` archive.
- Restores backups to original paths or to an alternate target root for another PC/user/machine.
- Keeps a backup browser and verification command for confidence before restore.

## Default backup location

The default backup folder is:

`F:\backup\gamesaves`

Each backup is created in a separate folder such as:

`F:\backup\gamesaves\Edge-of-Eternity-20260603-121732`

## Prerequisites

- Windows 10/11, or WSL with Windows drives mounted under `/mnt`.
- Python 3.10+.
- Optional for tests: `pytest`.

The app is intentionally mostly Python-standard-library so it can run on machines without a heavy install.

## Install / setup

From PowerShell:

```powershell
cd "F:\study\Windows\Applications\Gaming\SaveData\Backup\Tools\Python\universal-game-save-guardian"
python -m pip install -r requirements.txt
```

If PowerShell blocks scripts, use the one-liner below with `-ExecutionPolicy Bypass`.

## Run the polished desktop GUI

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\study\Windows\Applications\Gaming\SaveData\Backup\Tools\Python\universal-game-save-guardian\scripts\run-game-save-guardian.ps1" gui
```

In the GUI:

1. Confirm or change the backup destination.
2. Press **Discover Saves**.
3. Select one or more games in the beautiful list.
4. Inspect the right-side detail panel to verify paths and reasons.
5. Press **Backup Selected**.
6. Use **Backup Browser** to see created backups.

## CLI usage

Discover the currently running game:

```powershell
.\scripts\run-game-save-guardian.ps1 discover
```

Discover a specific game:

```powershell
.\scripts\run-game-save-guardian.ps1 discover --game "Edge of Eternity"
```

Discover all game saves across the PC and print JSON:

```powershell
.\scripts\run-game-save-guardian.ps1 discover --all --json
```

Create a backup for a specific game:

```powershell
.\scripts\run-game-save-guardian.ps1 backup --game "Edge of Eternity"
```

Create backups for all currently discovered save groups:

```powershell
.\scripts\run-game-save-guardian.ps1 backup --all
```

Change the default backup location:

```powershell
.\scripts\run-game-save-guardian.ps1 config --set-default "D:\MyGameSaveBackups"
```

List backups:

```powershell
.\scripts\run-game-save-guardian.ps1 list-backups
```

Verify a backup folder:

```powershell
.\scripts\run-game-save-guardian.ps1 verify "F:\backup\gamesaves\Edge-of-Eternity-YYYYMMDD-HHMMSS"
```

Preview restore actions without writing files:

```powershell
.\scripts\run-game-save-guardian.ps1 restore "F:\backup\gamesaves\Edge-of-Eternity-YYYYMMDD-HHMMSS" --dry-run
```

Restore to an alternate root for testing or another machine layout:

```powershell
.\scripts\run-game-save-guardian.ps1 restore "F:\backup\gamesaves\Edge-of-Eternity-YYYYMMDD-HHMMSS" --target-root "D:\RestorePreview"
```

Clear cached discovery results:

```powershell
.\scripts\run-game-save-guardian.ps1 scan-cache --clear
```

## Local web dashboard

```powershell
.\scripts\run-game-save-guardian.ps1 web --port 8765
```

Then open:

`http://127.0.0.1:8765`

The web UI is local-only by default and mirrors the discovery dashboard.

## Discovery strategy

The app combines several methods instead of relying on one database:

- Ludusavi manifest for known game save locations.
- Running game process detection.
- Steam library parsing from `libraryfolders.vdf` and `appmanifest_*.acf`.
- Epic Games manifest parsing.
- Standalone game folder hints from common drive-level game folders.
- Steam emulator / RUNE / Goldberg-style save hints where available.
- Fast save-root scans across Windows users and visible drives.
- Save-like file classifiers and junk-folder exclusions to avoid caches, logs, shaders, screenshots, crash dumps, and temporary files.

No tool can honestly guarantee perfect discovery of every possible game ever made, but this project is designed to go beyond manifest-only tools by combining known locations, live PC inspection, and heuristic fallback discovery.

## Important files

- `game-save-guardian.py` — main runnable entry point.
- `gamesave_guardian/app.py` — discovery, backup, restore, CLI, GUI, and web logic.
- `scripts/run-game-save-guardian.ps1` — PowerShell 5-compatible runner.
- `tests/test_guardian_core.py` — regression tests for path conversion, discovery, backup, restore, verification, and backup listing.
- `data/ludusavi_manifest.yaml` — local cache downloaded on first use; ignored by Git because public manifest snapshots can trigger provider secret scanning false positives.

## Troubleshooting

- If a game is not found, run the game once and then press **Discover Saves** again.
- If a game uses a custom location, add or back up that folder manually through the selected game details in a future rule update.
- If PowerShell says Python failed due to Windows Store aliases, use the included runner; it prefers `py.exe -3` and avoids broken WindowsApps aliases.
- Always run `restore --dry-run` before restoring to original paths.
- If a discovered item is not a game, simply do not select it for backup. The app shows confidence and reasons so false positives are visible.

## Development verification

```powershell
python -m py_compile game-save-guardian.py gamesave_guardian\app.py
python -m pytest -q
.\scripts\run-game-save-guardian.ps1 discover --game "Edge of Eternity"
.\scripts\run-game-save-guardian.ps1 discover --all --json
```
