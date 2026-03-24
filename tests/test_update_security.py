import hashlib
import json

from core import update_security


def test_extract_expected_sha256_prefers_matching_archive():
    text = (
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  other.tar.gz\n"
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb  update.tar.gz\n"
    )
    got = update_security._extract_expected_sha256(text, "update.tar.gz")
    assert got == "b" * 64


def test_verify_release_tarball_success(monkeypatch, tmp_path):
    tar = tmp_path / "update.tar.gz"
    tar.write_bytes(b"content")
    digest = hashlib.sha256(b"content").hexdigest()

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(req, timeout=10):
        url = req.full_url
        if "/releases/tags/" in url:
            payload = json.dumps(
                {"assets": [{"name": "SHA256SUMS", "browser_download_url": "https://example/SHA256SUMS"}]}
            ).encode("utf-8")
            return Response(payload)
        if url == "https://example/SHA256SUMS":
            return Response(f"{digest}  update.tar.gz\n".encode("utf-8"))
        raise AssertionError(url)

    monkeypatch.setattr(update_security.urllib.request, "urlopen", fake_urlopen)

    ok, msg = update_security.verify_release_tarball("5.7-alpha", tar)

    assert ok is True
    assert "подтвержд" in msg.lower()


def test_verify_release_tarball_fails_on_mismatch(monkeypatch, tmp_path):
    tar = tmp_path / "update.tar.gz"
    tar.write_bytes(b"content")

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(req, timeout=10):
        url = req.full_url
        if "/releases/tags/" in url:
            payload = json.dumps(
                {"assets": [{"name": "SHA256SUMS", "browser_download_url": "https://example/SHA256SUMS"}]}
            ).encode("utf-8")
            return Response(payload)
        if url == "https://example/SHA256SUMS":
            return Response(("f" * 64 + "  update.tar.gz\n").encode("utf-8"))
        raise AssertionError(url)

    monkeypatch.setattr(update_security.urllib.request, "urlopen", fake_urlopen)

    ok, msg = update_security.verify_release_tarball("5.7-alpha", tar)

    assert ok is False
    assert "sha256" in msg.lower()
