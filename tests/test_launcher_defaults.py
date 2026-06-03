import socket
from unittest import mock

from gamesave_guardian import app


def test_main_defaults_to_gui_when_no_arguments(monkeypatch):
    called = []
    monkeypatch.setattr(app, "launch_gui", lambda: called.append("gui"))

    app.main([])

    assert called == ["gui"]


def test_available_port_skips_blocked_preferred_port(monkeypatch):
    # Use an actual bound socket to simulate Windows WinError 10013 / unavailable 8765.
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocked_port = blocker.getsockname()[1]
    try:
        monkeypatch.setattr(app, "port_looks_like_michsavegame", lambda port: False)
        chosen = app.available_port(blocked_port, fallbacks=[blocked_port + 1, blocked_port + 2])
    finally:
        blocker.close()

    assert chosen != blocked_port


def test_serve_reuses_existing_michsavegame_instance(monkeypatch):
    opened = []
    monkeypatch.setattr(app, "port_looks_like_michsavegame", lambda port: True)
    monkeypatch.setattr(app.webbrowser, "open", lambda url: opened.append(url))

    app.serve(8765, open_browser=True)

    assert opened == ["http://127.0.0.1:8765/app"]
