from core import mirror


def test_build_auto_restore_script_uses_p_suffix_for_nvme(monkeypatch):
    monkeypatch.setattr(mirror, "is_uefi", lambda: True)
    info = {"type": "rsync"}

    script = mirror._build_auto_restore_script("/tmp/mirror", "/dev/nvme0n1", info)

    assert "mkfs.fat -F32 /dev/nvme0n1p1" in script
    assert "mkfs.ext4 -F /dev/nvme0n1p2" in script
    assert "mount /dev/nvme0n1p2 /mnt/target" in script


def test_detect_mirror_type_prefers_btrfs_recv(tmp_path):
    (tmp_path / ".snap_root_prev").mkdir()
    (tmp_path / "rootfs").mkdir()
    (tmp_path / "rootfs-2026-01-01.tar.gz").write_text("x", encoding="utf-8")
    (tmp_path / "partition_table.sfdisk").write_text("pt", encoding="utf-8")
    (tmp_path / "boot-efi.tar").write_text("efi", encoding="utf-8")

    result = mirror.detect_mirror_type(str(tmp_path))

    assert result is not None
    assert result["type"] == "btrfs_recv"
    assert result["subvols"] == ["root"]
    assert result["has_pt"] is True
    assert result["has_efi"] is True


def test_build_rsync_excludes_includes_optional_paths():
    excludes = mirror._build_rsync_excludes(["downloads"])

    assert "/home/*/.cache" in excludes
    assert "/home/*/Downloads" not in excludes
    assert "/var/lib/docker" in excludes
