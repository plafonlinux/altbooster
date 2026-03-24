import time

from core import borg


def test_archive_stats_dedup_bytes_parses_numeric_variants():
    assert borg.archive_stats_dedup_bytes({"deduplicated_size": 123}) == 123
    assert borg.archive_stats_dedup_bytes({"deduplicated": "456"}) == 456
    assert borg.archive_stats_dedup_bytes({"deduplicated_size": -1}) is None
    assert borg.archive_stats_dedup_bytes({"deduplicated": True}) is None


def test_parse_borg_json_stdout_with_prefix_noise():
    payload = "some text before\n{\"archives\": [{\"stats\": {\"deduplicated_size\": 42}}]}"

    result = borg._parse_borg_json_stdout(payload)

    assert result is not None
    assert result["archives"][0]["stats"]["deduplicated_size"] == 42


def test_write_borg_env_file_quotes_passphrase(monkeypatch, tmp_path):
    config_dir = tmp_path / "cfg"
    env_file = config_dir / "borg-env"
    ssh_key = config_dir / "borg_id_ed25519"

    monkeypatch.setattr(borg.config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(borg, "_BORG_ENV_FILE", env_file)
    monkeypatch.setattr(borg, "borg_ssh_key_path", lambda: ssh_key)
    monkeypatch.setattr(
        borg.config,
        "state_get",
        lambda key, default=None: "p@ss with spaces#and=symbols" if key == "borg_passphrase" else default,
    )

    ok = borg._write_borg_env_file()

    assert ok is True
    content = env_file.read_text(encoding="utf-8")
    assert content.splitlines()[0] == "BORG_PASSPHRASE='p@ss with spaces#and=symbols'"
    assert "BORG_RSH=ssh -i " in content
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"


def test_restore_flatpak_meta_fails_when_install_fails(monkeypatch, tmp_path):
    calls = []

    class Result:
        def __init__(self, returncode):
            self.returncode = returncode
            self.stdout = ""
            self.stderr = ""

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        if "install" in cmd:
            return Result(1)
        return Result(0)

    monkeypatch.setattr(borg.subprocess, "run", fake_run)
    monkeypatch.setattr(borg.GLib, "idle_add", lambda fn, *a: fn(*a))

    (tmp_path / "flatpak-remotes.txt").write_text("flathub https://flathub.org/repo/flathub.flatpakrepo\n", encoding="utf-8")
    (tmp_path / "flatpak-apps.txt").write_text("org.test.App\n", encoding="utf-8")

    done = []
    borg.restore_flatpak_meta(tmp_path, lambda _line: None, lambda ok: done.append(ok))
    for _ in range(50):
        if done:
            break
        time.sleep(0.01)

    assert done == [False]
    assert any("remote-add" in cmd for cmd in calls)
    assert any("install" in cmd for cmd in calls)
