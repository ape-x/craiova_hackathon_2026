"""No-clobber source release checks; no Docker, cloud or application calls."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from install_release import MANIFEST, install_release, verify_release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.releases = self.root / "releases"
        self.releases.mkdir()
        self.revision = "a" * 40
        self.target = self.releases / self.revision
        self.archive = self.root / "release.tar"
        self.make_archive()

    def make_archive(self, extra=None):
        with tarfile.open(self.archive, "w") as archive:
            for name, data, mode in (("backend/app/main.py", b"reviewed source", 0o644),
                                     ("deploy/google-cloud/deploy.sh", b"#!/bin/sh\nexit 0\n", 0o755)):
                item = tarfile.TarInfo(name)
                item.size, item.mode = len(data), mode
                archive.addfile(item, io.BytesIO(data))
            if extra is not None:
                archive.addfile(extra, io.BytesIO(b"x" * extra.size))

    def install(self):
        return install_release(self.archive, self.releases, self.revision,
                               hashlib.sha256(self.archive.read_bytes()).hexdigest())

    def test_verified_archive_installs_exact_source_and_executable_permissions(self):
        receipt = self.install()
        self.assertEqual(receipt, verify_release(self.target, self.revision))
        self.assertEqual(receipt["files"], 2)
        self.assertEqual((self.target / "backend/app/main.py").read_bytes(), b"reviewed source")
        self.assertEqual((self.target / "deploy/google-cloud/deploy.sh").stat().st_mode & 0o777, 0o755)

    def test_existing_release_with_leftover_source_is_never_overlaid(self):
        self.target.mkdir()
        (self.target / "REVISION").write_text(self.revision + "\n")
        leftover = self.target / "old-source.py"
        leftover.write_text("must not be labelled a clean release")
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(leftover.read_text(), "must not be labelled a clean release")
        self.assertFalse((self.target / MANIFEST).exists())

    def test_second_install_never_overwrites_valid_release(self):
        self.install()
        with self.assertRaises(FileExistsError):
            self.install()
        verify_release(self.target, self.revision)

    def test_unreviewed_archive_checksum_is_rejected_before_creating_directory(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            install_release(self.archive, self.releases, self.revision, "0" * 64)
        self.assertFalse(self.target.exists())

    def test_changed_added_removed_or_repermissioned_source_cannot_use_clean_marker(self):
        for index, mutation in enumerate(("changed", "added", "removed", "permissions")):
            with self.subTest(mutation=mutation):
                revision = str(index) * 40
                target = self.releases / revision
                install_release(self.archive, self.releases, revision,
                                hashlib.sha256(self.archive.read_bytes()).hexdigest())
                source = target / "backend/app/main.py"
                if mutation == "changed":
                    source.write_bytes(b"dirty source")
                elif mutation == "added":
                    (target / "leftover.py").write_text("extra source")
                elif mutation == "removed":
                    source.unlink()
                else:
                    source.chmod(0o777)
                with self.assertRaisesRegex(ValueError, "Release"):
                    verify_release(target, revision)

    def test_changed_revision_marker_is_not_authoritative(self):
        self.install()
        new_revision = "b" * 40
        (self.target / "REVISION").write_text(new_revision + "\n")
        with self.assertRaisesRegex(ValueError, "manifest"):
            verify_release(self.target, new_revision)

    def test_traversal_links_duplicate_entries_and_reserved_metadata_abort_install(self):
        for name, kind in (("../outside", tarfile.REGTYPE), ("backend/link", tarfile.SYMTYPE),
                           ("backend/hardlink", tarfile.LNKTYPE), ("backend/app/main.py", tarfile.REGTYPE),
                           (MANIFEST, tarfile.REGTYPE), ("REVISION", tarfile.REGTYPE)):
            with self.subTest(name=name):
                item = tarfile.TarInfo(name)
                item.type, item.linkname = kind, "../outside"
                self.make_archive(item)
                with self.assertRaises(ValueError):
                    self.install()
                self.assertFalse(self.target.exists())
                self.assertFalse((self.root / "outside").exists())

    def test_missing_or_malformed_manifest_is_rejected(self):
        self.install()
        (self.target / MANIFEST).write_text(json.dumps([]))
        with self.assertRaisesRegex(ValueError, "manifest"):
            verify_release(self.target, self.revision)
        (self.target / MANIFEST).unlink()
        with self.assertRaisesRegex(ValueError, "manifest"):
            verify_release(self.target, self.revision)

    def test_real_committed_git_archive_can_be_installed_and_verified_offline(self):
        repository = Path(__file__).resolve().parents[2]
        revision = "69b252a9209c43f97de5b418e62a6074f66d280e"
        with self.archive.open("wb") as stream:
            subprocess.run(["git", "archive", "--format=tar", revision], cwd=repository, stdout=stream, check=True)
        receipt = install_release(self.archive, self.releases, revision,
                                  hashlib.sha256(self.archive.read_bytes()).hexdigest())
        self.assertEqual(receipt, verify_release(self.releases / revision, revision))
        self.assertTrue((self.releases / revision / "backend/app/main.py").is_file())


if __name__ == "__main__":
    unittest.main()
