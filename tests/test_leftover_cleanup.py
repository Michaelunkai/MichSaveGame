import json
from pathlib import Path

from gamesave_guardian import app


def test_discover_game_leftovers_scans_c_drive_common_roots(monkeypatch, tmp_path):
    c_drive = tmp_path / "c"
    leftover = c_drive / "Users" / "Till" / "AppData" / "Local" / "Edge Of Eternity" / "cache.bin"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"junk")
    unrelated = c_drive / "Users" / "Till" / "AppData" / "Local" / "Other Game" / "cache.bin"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"keep")

    monkeypatch.setattr(app, "c_drive_root", lambda: c_drive)
    monkeypatch.setattr(app, "windows_profiles", lambda: [c_drive / "Users" / "Till"])

    result = app.discover_game_leftovers("Edge of Eternity")

    assert result["game"] == "Edge of Eternity"
    assert result["count"] == 1
    candidate = result["candidates"][0]
    assert candidate["path"].endswith("Edge Of Eternity")
    assert candidate["path_windows"].startswith("C:")
    assert candidate["file_count"] == 1
    assert candidate["delete_safe"] is True


def test_cleanup_game_leftovers_is_preview_first_and_quarantines_before_delete(monkeypatch, tmp_path):
    c_drive = tmp_path / "c"
    target = c_drive / "Users" / "Till" / "AppData" / "Local" / "Edge Of Eternity"
    target.mkdir(parents=True)
    (target / "cache.bin").write_bytes(b"junk")
    backup_root = tmp_path / "backups"

    monkeypatch.setattr(app, "c_drive_root", lambda: c_drive)
    monkeypatch.setattr(app, "windows_profiles", lambda: [c_drive / "Users" / "Till"])
    monkeypatch.setattr(app, "backup_root", lambda: backup_root)

    preview = app.cleanup_game_leftovers("Edge of Eternity", execute=False)
    assert preview["ok"] is True
    assert preview["mode"] == "preview"
    assert target.exists()

    deleted = app.cleanup_game_leftovers("Edge of Eternity", execute=True)
    assert deleted["ok"] is True
    assert deleted["mode"] == "deleted"
    assert not target.exists()
    manifest = Path(deleted["quarantine_path"]) / "cleanup_manifest.json"
    assert manifest.exists()
    assert json.loads(manifest.read_text())["game"] == "Edge of Eternity"
    assert list(Path(deleted["quarantine_path"]).rglob("cache.bin"))


def test_cleanup_api_recomputes_candidates_and_ignores_client_paths(monkeypatch, tmp_path):
    safe = tmp_path / "c" / "Users" / "Till" / "AppData" / "Local" / "Fixture Quest"
    safe.mkdir(parents=True)
    (safe / "leftover.dat").write_bytes(b"safe")
    monkeypatch.setattr(app, "c_drive_root", lambda: tmp_path / "c")
    monkeypatch.setattr(app, "windows_profiles", lambda: [tmp_path / "c" / "Users" / "Till"])
    monkeypatch.setattr(app, "backup_root", lambda: tmp_path / "backups")

    result = app.api_cleanup_leftovers({
        "game": "Fixture Quest",
        "execute": False,
        "paths": [r"C:\Users\Till\Documents\passwords"],
    })

    assert result["ok"] is True
    assert result["count"] == 1
    assert "passwords" not in json.dumps(result).lower()


def test_cleanup_api_empty_candidate_ids_deletes_nothing_until_preview_selection(monkeypatch, tmp_path):
    safe = tmp_path / "c" / "Users" / "Till" / "AppData" / "Local" / "Fixture Quest"
    safe.mkdir(parents=True)
    (safe / "leftover.dat").write_bytes(b"safe")
    monkeypatch.setattr(app, "c_drive_root", lambda: tmp_path / "c")
    monkeypatch.setattr(app, "windows_profiles", lambda: [tmp_path / "c" / "Users" / "Till"])
    monkeypatch.setattr(app, "backup_root", lambda: tmp_path / "backups")

    result = app.api_cleanup_leftovers({"game": "Fixture Quest", "execute": True, "candidate_ids": []})

    assert result["ok"] is True
    assert result["count"] == 0
    assert safe.exists()


def test_shell_is_rebranded_for_michsavegame_and_has_cleanup_workflow():
    html = app.render_app_shell()

    assert "MichSaveGame" in html
    assert "Delete C-drive leftovers" in html
    assert "fetch('/api/leftovers?" in html
    assert "fetch('/api/delete-leftovers'" in html
    assert "authHeaders" in html
    assert "jsonHeaders" in html
    assert "Beautiful, safe cleanup" in html
