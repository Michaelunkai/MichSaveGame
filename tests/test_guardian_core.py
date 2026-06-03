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


def test_discover_all_does_not_substring_blacklist_real_game_titles(monkeypatch, tmp_path):
    # Regression: broad substring filtering used to hide real game names such as
    # Edge of Eternity because the slug contained "edge".
    root = tmp_path / "Users" / "Till" / "Saved Games"
    save_dir = root / "Edge of Eternity" / "Saves"
    save_dir.mkdir(parents=True)
    (save_dir / "latest.sav").write_bytes(b"latest")

    monkeypatch.setattr(app, "all_save_roots", lambda: [root])
    monkeypatch.setattr(app, "installed_game_hints", lambda: [])
    monkeypatch.setattr(app, "visible_drives", lambda: [])
    monkeypatch.setattr(app, "CONFIG_DIR", tmp_path / "config")

    games = app.discover_all_games(refresh=True)

    assert any(g["title"] == "Edge of Eternity" for g in games)


def test_infer_game_titles_are_human_readable_from_game_install_trees(tmp_path):
    games_root = tmp_path / "games"
    save_path = games_root / "sea-of-stars" / "SeaOfStars_Data" / "Saves"
    save_path.mkdir(parents=True)

    assert app.infer_game_from_path(save_path, games_root) == "Sea Of Stars"
    assert app.infer_game_from_path(save_path, tmp_path) == "Sea Of Stars"


def test_installed_game_hint_maps_nested_asset_save_folder_to_game_title(tmp_path):
    install = tmp_path / "games" / "Three Minutes To Eight"
    nested = install / "Three Minutes To Eight_Data" / "res" / "Saves"
    nested.mkdir(parents=True)

    title = app.infer_game_from_installed_path(nested, [{"title": "Three Minutes To Eight", "install_path": str(install)}])

    assert title == "Three Minutes To Eight"


def test_discover_all_prunes_own_backup_and_study_output_trees(monkeypatch, tmp_path):
    drive = tmp_path / "f"
    real = drive / "Games" / "Real Game" / "Saves"
    fake_backup = drive / "backup" / "gamesaves" / "Old" / "payload" / "source-01-Saves"
    fake_study = drive / "study" / "Project" / "Saves"
    for folder in (real, fake_backup, fake_study):
        folder.mkdir(parents=True)
        (folder / "slot.sav").write_bytes(b"save")

    monkeypatch.setattr(app, "visible_drives", lambda: [drive])
    monkeypatch.setattr(app, "windows_profiles", lambda: [])
    monkeypatch.setattr(app, "steam_roots", lambda: [])
    monkeypatch.setattr(app, "installed_game_hints", lambda: [])
    monkeypatch.setattr(app, "backup_root", lambda: fake_backup.parents[2])
    monkeypatch.setattr(app, "ROOT", drive / "study")
    monkeypatch.setattr(app, "CONFIG_DIR", tmp_path / "config")

    games = app.discover_all_games(refresh=True)
    titles = {g["title"] for g in games}

    assert "Real Game" in titles
    assert "Old" not in titles
    assert "Project" not in titles


def test_verify_and_restore_accept_windows_style_payload_separators(tmp_path):
    backup_dir = tmp_path / "Game-20260101-010101"
    payload = backup_dir / "payload" / "source-01-Saves"
    payload.mkdir(parents=True)
    (payload / "slot.sav").write_bytes(b"save")
    (backup_dir / "backup_manifest.json").write_text(json.dumps({
        "game": "Game",
        "created_at": "2026-01-01T01:01:01",
        "sources": [{
            "payload": "payload\\source-01-Saves",
            "file_count": 1,
            "byte_count": 4,
            "restore_to": r"C:\Users\Till\Saved Games\Game\Saves",
        }],
    }), encoding="utf-8")

    assert app.verify_backup(str(backup_dir))["ok"] is True
    restore_root = tmp_path / "restore"
    app.restore(str(backup_dir), str(restore_root), dry_run=False)
    assert list(restore_root.rglob("slot.sav"))[0].read_bytes() == b"save"


def test_build_plan_uses_installed_hint_steam_emu_for_not_running_game(monkeypatch, tmp_path):
    install = tmp_path / "Games" / "edge-of-eternity"
    save_root = tmp_path / "Users" / "Public" / "Documents" / "Steam" / "RUNE" / "269190"
    install.mkdir(parents=True)
    save_root.mkdir(parents=True)
    (save_root / "slot.sav").write_bytes(b"save")
    (install / "steam_emu.ini").write_text(f"Game data is stored at {save_root}\n", encoding="utf-8")
    monkeypatch.setattr(app, "detect_running_game", lambda: (None, None))
    monkeypatch.setattr(app, "extract_manifest_sources", lambda game: (game, [], None))
    monkeypatch.setattr(app, "installed_game_hints", lambda: [{"title": "edge-of-eternity", "platform": "Standalone", "install_path": str(install)}])
    monkeypatch.setattr(app, "heuristic_sources", lambda game, install_dir=None: [])
    monkeypatch.setattr(app, "visible_drives", lambda: [])

    plan = app.build_plan("Edge of Eternity")

    assert any(src.path == save_root and src.file_count == 1 for src in plan.sources)
