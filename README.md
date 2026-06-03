# Universal Game Save Guardian

Universal Game Save Guardian is a Windows-focused game save backup and restore application inspired by GameSave Manager and Ludusavi, with three entry points: CLI, Tkinter desktop GUI, and a tiny local web UI.

It uses the public Ludusavi manifest plus extra heuristics for games that are missing from the manifest: currently running game process detection, Steam library/appid paths, Steam emulator save paths such as RUNE `steam_emu.ini`, and common Windows save roots (`Documents\My Games`, `Saved Games`, `AppData\Local`, `AppData\LocalLow`, `AppData\Roaming`, and public Steam documents).

## Default backup location

The default backup folder is:

`F:\backup\gamesaves`

Each backup is created in its own timestamped folder and also archived as a `.tar.gz` beside it.

## Prerequisites

- Windows 10/11 or WSL with access to Windows drives.
- Python 3.10+ on PATH.
- Optional: `pip install -r requirements.txt` if you want YAML tooling later. The bundled app works with the included text manifest and Python standard library.

## Setup

```powershell
cd "F:\study\Windows\Applications\Gaming\SaveData\Backup\Tools\Python\universal-game-save-guardian"
python -m pip install -r requirements.txt
```

## CLI usage

Discover the currently running game:

```powershell
.\scripts\run-game-save-guardian.ps1 discover
```

Discover a specific game:

```powershell
.\scripts\run-game-save-guardian.ps1 discover --game "Edge of Eternity"
```

Create a backup in the default folder:

```powershell
.\scripts\run-game-save-guardian.ps1 backup --game "Edge of Eternity"
```

Change the default backup location:

```powershell
.\scripts\run-game-save-guardian.ps1 config --set-default "D:\MyGameSaveBackups"
```

Restore a backup to the original machine paths:

```powershell
.\scripts\run-game-save-guardian.ps1 restore "F:\backup\gamesaves\Edge-of-Eternity-YYYYMMDD-HHMMSS"
```

Dry-run a restore first:

```powershell
.\scripts\run-game-save-guardian.ps1 restore "F:\backup\gamesaves\Edge-of-Eternity-YYYYMMDD-HHMMSS" --dry-run
```

## GUI usage

```powershell
.\scripts\run-game-save-guardian.ps1 gui
```

The GUI lets you enter a game name, browse for a backup location, discover save paths, back up saves, and restore a selected backup.

## Local web UI

```powershell
.\scripts\run-game-save-guardian.ps1 web --port 8765
```

Then open `http://127.0.0.1:8765`.

## Inputs and outputs

- Input: game title or blank to detect the currently running game.
- Output: a backup folder containing `backup_manifest.json`, copied save payloads, and a compressed `.tar.gz` archive.
- Restore safety: if the destination already exists, the app creates a safety copy inside the selected backup folder before replacing files.

## Important files

- `game-save-guardian.py` — main runnable CLI/GUI/web entry point.
- `gamesave_guardian/app.py` — application logic.
- `scripts/run-game-save-guardian.ps1` — PowerShell runner.
- `data/ludusavi_manifest.yaml` — bundled public Ludusavi manifest snapshot.

## Troubleshooting

- If a game is missing from the manifest, run it first and then run `discover` with no `--game`; the app uses the running executable path and common save roots.
- For cracked/Steam-emulated games, keep `steam_emu.ini` beside the game executable; the app reads comments such as `Game data is stored at ...`.
- Always test `restore --dry-run` before restoring to another PC.
- If Python is not found in PowerShell, install Python from python.org or the Microsoft Store and reopen PowerShell.
