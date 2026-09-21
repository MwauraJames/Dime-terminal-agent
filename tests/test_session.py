import json

from conftest import load_dime


def test_session_round_trip(tmp_path, monkeypatch):
    s = load_dime().mods.session
    monkeypatch.setattr(s, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(s, "SESSION_FILE", tmp_path / "cache" / "last_session.json")
    assert s.load_session() is None                                   # nothing saved yet
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "héllo"}]
    s.save_session(msgs)
    assert s.load_session() == msgs


def test_corrupt_session_is_ignored(tmp_path, monkeypatch):
    s = load_dime().mods.session
    monkeypatch.setattr(s, "SESSION_FILE", tmp_path / "last_session.json")
    (tmp_path / "last_session.json").write_text("{not json")
    assert s.load_session() is None


def test_save_failure_never_raises(monkeypatch):
    s = load_dime().mods.session
    monkeypatch.setattr(s, "CACHE_DIR", type("P", (), {"mkdir": lambda *a, **k: (_ for _ in ()).throw(OSError("read-only"))})())
    s.save_session([{"role": "user", "content": "x"}])               # must not raise
