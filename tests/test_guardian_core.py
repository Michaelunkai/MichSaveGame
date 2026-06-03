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
