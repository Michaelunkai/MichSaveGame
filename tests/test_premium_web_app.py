from gamesave_guardian import app


def test_premium_shell_contains_modern_app_markers():
    html = app.render_app_shell()

    assert "id=\"app-shell\"" in html
    assert "SaveVault Command Center" in html
    assert "--bg:" in html
    assert "backdrop-filter" in html
    assert "data-action=\"discover\"" in html
    assert "Restore Preview" in html
    assert "Activity Timeline" in html


def test_app_shell_has_client_side_workflow_scripts():
    html = app.render_app_shell()

    for marker in ["fetch('/api/discover?", "fetch('/api/backups'", "renderGames", "selectedGames", "toast("]:
        assert marker in html


def test_api_payload_helpers_are_stable(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(app, "discover_all_games", lambda refresh=False: [{
        "title": "Fixture Quest",
        "platform": "Steam",
        "location_count": 2,
        "size_human": "12.0 KB",
        "byte_count": 12288,
        "latest_write_iso": "2026-01-01T12:00:00",
        "confidence": 92,
        "sources": [],
    }])
    monkeypatch.setattr(app, "list_backups", lambda root=None: [{
        "game": "Fixture Quest",
        "path_windows": r"F:\backup\gamesaves\Fixture-Quest-1",
        "created_at": "2026-01-01T12:30:00",
        "size_human": "12.0 KB",
        "sources": 2,
    }])

    assert app.api_discover()["games"][0]["title"] == "Fixture Quest"
    assert app.api_backups()["backups"][0]["game"] == "Fixture Quest"


def test_backup_api_recomputes_selected_games_and_ignores_client_sources(monkeypatch, tmp_path):
    safe_record = {"title": "Fixture Quest", "sources": [{"path": str(tmp_path / "safe")}]}
    captured = []

    monkeypatch.setattr(app, "discover_all_games", lambda refresh=False: [safe_record])
    monkeypatch.setattr(app, "backup_record", lambda rec, destination=None: captured.append((rec, destination)) or tmp_path / "backup")

    result = app.api_backup_selected({
        "destination": r"F:\backup\gamesaves",
        "titles": ["Fixture Quest"],
        "games": [{"title": "Fixture Quest", "sources": [{"path": r"C:\Users\micha\secret"}]}],
    })

    assert result["ok"] is True
    assert captured == [(safe_record, r"F:\backup\gamesaves")]


def test_backup_api_rejects_unknown_titles(monkeypatch):
    monkeypatch.setattr(app, "discover_all_games", lambda refresh=False: [])

    result = app.api_backup_selected({"titles": ["Not Real"]})

    assert result["ok"] is False
    assert "No discovered games" in result["error"]


def test_shell_embeds_csrf_token_and_escapes_reason():
    html = app.render_app_shell()

    assert "X-UGSG-Token" in html
    assert "selectedTitles" in html
    assert "escapeHtml((g.sources||[])[0]?.reason" in html


def test_shell_has_non_cutoff_full_game_list_ui():
    html = app.render_app_shell()

    assert "overflow:auto" in html
    assert "id=\"listMeta\"" in html
    assert "Showing ${rows.length} of ${games.length} save groups" in html
    assert "rows.slice" not in html
    assert "games.slice" not in html




def test_web_json_suppresses_client_abort_without_traceback():
    handler = app.Web.__new__(app.Web)
    calls = []
    handler.send_response = lambda code: calls.append(("status", code))
    handler.send_header = lambda key, value: calls.append(("header", key, value))
    handler.end_headers = lambda: calls.append(("end",))
    class AbortingWriter:
        def write(self, data):
            raise ConnectionAbortedError("client closed")
    handler.wfile = AbortingWriter()

    handler._json({"ok": True})

    assert calls and calls[0] == ("status", 200)
