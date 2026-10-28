# SPDX-License-Identifier: GPL-3.0-or-later
# Maintainer: Timofei Granovskii <t.stifix@gmail.com>

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import config


class UserdataSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = SimpleNamespace(config=str(self.root / "waydroid.cfg"))
        self.user_state = self.root / "home" / "waydroid"
        self.default_data = str(self.user_state / "data")
        self.defaults = dict(config.session_defaults)
        self.defaults.update(user_id="1001", waydroid_user_state=str(self.user_state),
                             waydroid_data=self.default_data)
        patcher = patch.dict(config.session_defaults, self.defaults, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_mapping(self, path, uid="1001"):
        Path(self.args.config).write_text("[userdata]\n{} = {}\n".format(uid, path))

    def test_unconfigured_user_keeps_the_standard_data_path(self):
        config.configure_session(self.args)
        self.assertEqual(config.session_defaults, self.defaults)
        self.assertFalse(Path(self.default_data).exists())

    def test_mapping_for_another_user_does_not_change_this_user(self):
        self.write_mapping("/missing/other-user-data", uid="1002")
        config.configure_session(self.args)
        self.assertEqual(config.session_defaults, self.defaults)

    def test_explicit_data_preserves_desktop_paths_content_and_ownership(self):
        data = self.root / "existing-data"
        data.mkdir(mode=0o711)
        marker = data / "existing-app-data"
        marker.write_bytes(b"preserve Android app state\x00")
        before = {p: p.stat() for p in (data, marker)}
        self.write_mapping(data)

        with patch("os.chown", side_effect=AssertionError("userdata must not be chowned")):
            config.configure_session(self.args)

        expected = dict(self.defaults, waydroid_data=str(data))
        self.assertEqual(config.session_defaults, expected)
        self.assertEqual(marker.read_bytes(), b"preserve Android app state\x00")
        for path, old in before.items():
            new = path.stat()
            self.assertEqual((new.st_ino, new.st_uid, new.st_gid, new.st_mode),
                             (old.st_ino, old.st_uid, old.st_gid, old.st_mode))
        self.assertFalse(Path(self.default_data).exists())

    def test_missing_explicit_data_fails_without_creating_a_profile(self):
        missing = self.root / "missing-data"
        self.write_mapping(missing)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            config.configure_session(self.args)
        self.assertFalse(missing.exists())
        self.assertEqual(config.session_defaults, self.defaults)

    def test_files_and_filesystem_root_are_rejected(self):
        file = self.root / "not-a-directory"
        file.touch()
        for path in ("/", "/tmp/..", str(file)):
            with self.subTest(path=path):
                self.write_mapping(path)
                with self.assertRaises(ValueError):
                    config.configure_session(self.args)

    def test_relative_and_multiline_paths_are_rejected(self):
        for path in ("relative/path", "/tmp/path\ncontinued", "/tmp/path\x00", "/tmp/path\x7f", "/tmp/with space"):
            with self.subTest(path=repr(path)):
                with self.assertRaises(ValueError):
                    config._userdata_path(path)

    def test_literal_percent_in_directory_name_is_not_interpolated(self):
        data = self.root / "data%literal"
        data.mkdir()
        self.write_mapping(data)
        config.configure_session(self.args)
        self.assertEqual(config.session_defaults["waydroid_data"], str(data))

    def test_symlink_is_canonicalized_and_root_alias_is_rejected(self):
        data = self.root / "existing-data"
        data.mkdir()
        link = self.root / "data-link"
        link.symlink_to(data, target_is_directory=True)
        self.write_mapping(link)
        config.configure_session(self.args)
        self.assertEqual(config.session_defaults["waydroid_data"], str(data))
        link.unlink()
        link.symlink_to("/", target_is_directory=True)
        with self.assertRaises(ValueError):
            config.configure_session(self.args)

    def test_container_rejects_data_outside_the_authenticated_user_mapping(self):
        allowed = self.root / "allowed-data"
        supplied = self.root / "different-data"
        allowed.mkdir()
        supplied.mkdir()
        self.write_mapping(allowed)
        session = {"user_id": "1001", "waydroid_data": str(supplied)}
        with self.assertRaisesRegex(ValueError, "does not match"):
            config.validate_session_userdata(self.args, session)
        self.assertEqual(session["waydroid_data"], str(supplied))

    def test_symlink_target_must_also_be_safe_for_lxc_mount_entries(self):
        data = self.root / "data with spaces"
        data.mkdir()
        link = self.root / "data-link"
        link.symlink_to(data, target_is_directory=True)
        self.write_mapping(link)
        with self.assertRaises(ValueError):
            config.configure_session(self.args)

    def test_container_accepts_and_canonicalizes_the_configured_directory(self):
        data = self.root / "existing-data"
        data.mkdir()
        link = self.root / "data-link"
        link.symlink_to(data, target_is_directory=True)
        self.write_mapping(data)
        session = {"user_id": "1001", "waydroid_data": str(link)}
        config.validate_session_userdata(self.args, session)
        self.assertEqual(session["waydroid_data"], str(data))

    def test_container_without_a_mapping_keeps_upstream_behavior(self):
        session = {"user_id": "1001", "waydroid_data": self.default_data}
        config.validate_session_userdata(self.args, session)
        self.assertEqual(session["waydroid_data"], self.default_data)

    def test_invalid_unix_user_id_is_rejected(self):
        for uid in ("-1", "1001\n", "root", "4294967296", "١٠٠١"):
            with self.subTest(uid=repr(uid)):
                with self.assertRaises(ValueError):
                    config.configured_userdata(self.args, uid)

    def test_removing_the_mapping_restores_the_standard_default(self):
        data = self.root / "existing-data"
        data.mkdir()
        self.write_mapping(data)
        config.configure_session(self.args)
        Path(self.args.config).unlink()
        config.configure_session(self.args)
        self.assertEqual(config.session_defaults, self.defaults)


if __name__ == "__main__":
    unittest.main()
