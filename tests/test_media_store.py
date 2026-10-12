from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from final_framework.knowledge import FilesystemMediaStore, MediaStoreError


class FilesystemMediaStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = FilesystemMediaStore(Path(self.tempdir.name) / "media")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_stores_bytes_at_media_id_object_key(self) -> None:
        object_key = self.store.put("media_abc", "r1", "image/jpeg", b"first image")

        self.assertEqual(object_key, "media_abc.r1.jpg")
        self.assertEqual(self.store.read(object_key), b"first image")
        self.assertFalse(self.store.local_file_missing(object_key))

    def test_versions_can_coexist_until_old_object_is_explicitly_removed(self) -> None:
        old_key = self.store.put("media_abc", "r1", "image/jpeg", b"old")
        new_key = self.store.put("media_abc", "r2", "image/png", b"new")

        self.assertEqual(new_key, "media_abc.r2.png")
        self.assertEqual(self.store.read(old_key), b"old")
        self.assertEqual(self.store.read(new_key), b"new")
        self.store.delete(old_key)
        self.assertTrue(self.store.local_file_missing(old_key))

    def test_reports_missing_file_without_hiding_the_media_record(self) -> None:
        object_key = self.store.put("media_abc", "r1", "image/webp", b"image")
        (self.store.root / object_key).unlink()

        self.assertTrue(self.store.local_file_missing(object_key))
        with self.assertRaisesRegex(FileNotFoundError, "Local media file is missing"):
            self.store.read(object_key)

    def test_rejects_invalid_id_mime_and_empty_bytes(self) -> None:
        with self.assertRaisesRegex(MediaStoreError, "media_id"):
            self.store.put("../escape", "r1", "image/jpeg", b"image")
        with self.assertRaisesRegex(MediaStoreError, "Unsupported"):
            self.store.put("media_abc", "r1", "application/pdf", b"file")
        with self.assertRaisesRegex(MediaStoreError, "non-empty"):
            self.store.put("media_abc", "r1", "image/jpeg", b"")
