import json
from pathlib import Path

import pytest

from gamesave_guardian import app


def test_path_translation_round_trip_for_windows_drive():
    wsl = app.win_to_wsl(r"F:\backup\gamesaves")
    assert wsl == "/mnt/f/backup/gamesaves"
    assert app.wsl_to_win(wsl) == r"F:\backup\gamesaves"


def test_discover_all_finds_save_like_game_folder(monkeypatch, tmp_path):
    root = tmp_path / "Users" / "Till" / "Saved Games"
    save_dir = root / "Great RPG" / "Saves"
    save_dir.mkdir(parents=True)
    (save_dir / "slot1.sav").write_bytes(b"save-data")

    monkeypatch.setattr(app, "all_save_roots", lambda: [root])
    monkeypatch.setattr(app, "installed_game_hints", lambda: [])
    monkeypatch.setattr(app, "CONFIG_DIR", tmp_path / "config")

    games = app.discover_all_games(refresh=True)

    assert any(g["title"] == "Great RPG" for g in games)
    great = next(g for g in games if g["title"] == "Great RPG")
    assert great["location_count"] == 1
    assert great["file_count"] == 1
    assert great["sources"][0]["path"].endswith("Great RPG/Saves")


def test_backup_restore_and_verify_selected_record(monkeypatch, tmp_path):
    source = tmp_path / "Users" / "Till" / "Saved Games" / "Edge" / "Saves"
    source.mkdir(parents=True)
    (source / "save0.sav").write_bytes(b"latest save bytes")
    dest = tmp_path / "backups"

    rec = {
        "title": "Edge Test",
        "platform": "fixture",
        "manifest_hit": False,
        "sources": [app.source_json(app.Source(source, "fixture", ["save"], True, *app.summarize_path(source)))],
    }

    backup_dir = app.backup_record(rec, str(dest))
    assert (backup_dir / "backup_manifest.json").exists()
    assert app.verify_backup(str(backup_dir))["ok"] is True

    restore_root = tmp_path / "restore-root"
    actions = app.restore(str(backup_dir), str(restore_root), dry_run=False)
    assert actions
    restored_files = list(restore_root.rglob("save0.sav"))
    assert restored_files
    assert restored_files[0].read_bytes() == b"latest save bytes"


def test_verify_backup_fails_when_payload_deleted(tmp_path):
    b = tmp_path / "Game-20260101-010101"
    payload = b / "payload" / "source-01-Saves"
    payload.mkdir(parents=True)
    (payload / "save0.sav").write_bytes(b"save")
    (b / "backup_manifest.json").write_text(json.dumps({
        "game": "Game",
        "created_at": "2026-01-01T01:01:01",
        "sources": [{"payload": "payload/source-01-Saves", "file_count": 1, "byte_count": 4}],
    }), encoding="utf-8")

    assert app.verify_backup(str(b))["ok"] is True
    (payload / "save0.sav").unlink()

    result = app.verify_backup(str(b))
    assert result["ok"] is False
    assert result["mismatched"]


def test_list_backups_reads_metadata(tmp_path):
    b = tmp_path / "Game-20260101-010101"
    b.mkdir()
    (b / "backup_manifest.json").write_text(json.dumps({"game": "Game", "created_at": "2026-01-01T01:01:01", "sources": []}), encoding="utf-8")

    backups = app.list_backups(str(tmp_path))

    assert backups[0]["game"] == "Game"
    assert backups[0]["path"].endswith("Game-20260101-010101")


def test_all_save_roots_include_common_roots_on_every_visible_drive(monkeypatch, tmp_path):
    drive_x = tmp_path / "x"
    drive_y = tmp_path / "y"
    for d in (drive_x, drive_y):
        (d / "Users" / "Till" / "Saved Games").mkdir(parents=True)
        (d / "ProgramData").mkdir()
    monkeypatch.setattr(app, "visible_drives", lambda: [drive_x, drive_y])
    monkeypatch.setattr(app, "steam_roots", lambda: [])

    roots = {str(p) for p in app.all_save_roots()}

    assert str(drive_x / "Users" / "Till" / "Saved Games") in roots
    assert str(drive_y / "Users" / "Till" / "Saved Games") in roots
    assert str(drive_x / "ProgramData") in roots
    assert str(drive_y / "ProgramData") in roots


def test_build_plan_uses_drivewide_fallback_when_manifest_and_known_roots_miss(monkeypatch, tmp_path):
    save_dir = tmp_path / "weird_drive" / "Deep" / "Nested" / "Edge of Eternity" / "LatestSaves"
    save_dir.mkdir(parents=True)
    (save_dir / "latest.sav").write_bytes(b"latest")
    monkeypatch.setattr(app, "detect_running_game", lambda: (None, None))
    monkeypatch.setattr(app, "extract_manifest_sources", lambda game: (game, [], None))
    monkeypatch.setattr(app, "heuristic_sources", lambda game, install_dir=None: [])
    monkeypatch.setattr(app, "visible_drives", lambda: [tmp_path / "weird_drive"])

    plan = app.build_plan("Edge of Eternity")

    assert any(s.path == save_dir and s.exists and s.file_count == 1 for s in plan.sources)
    assert any("all-visible-drive" in s.reason for s in plan.sources)
