import tempfile
import unittest
from pathlib import Path

from final_framework.knowledge import FilesystemMediaStore, MediaReferenceStore, MediaReferenceStoreError


class MediaReferenceStoreTests(unittest.TestCase):
    def test_registers_worker_media_and_scopes_it_to_session(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            media_store = FilesystemMediaStore(Path(root))
            object_key = media_store.put("media_a", "r1", "image/jpeg", b"image")
            references = MediaReferenceStore(media_store)
            registered = references.register_worker_result(session_id="session-a", result={
                "items": [{
                    "candidate_id": "media_a",
                    "package_id": "wikipedia_vi",
                    "payload": {
                        "media_id": "media_a",
                        "object_key": object_key,
                        "mime_type": "image/jpeg",
                        "caption": "Portrait",
                        "source_uri": "https://example.test/image",
                    },
                }],
            })
            self.assertEqual(registered, ("media_a",))
            reference = references.get(session_id="session-a", media_id="media_a")
            self.assertEqual(reference.object_key, object_key)
            self.assertEqual(reference.url, f"/knowledge/media/{object_key}")
            with self.assertRaises(MediaReferenceStoreError):
                references.get(session_id="session-b", media_id="media_a")

    def test_marks_missing_binary_without_hiding_reference(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            media_store = FilesystemMediaStore(Path(root))
            references = MediaReferenceStore(media_store)
            references.register_worker_result(session_id="session-a", result={
                "items": [{
                    "candidate_id": "media_missing",
                    "package_id": "wikipedia_vi",
                    "payload": {
                        "media_id": "media_missing",
                        "object_key": "media_missing.r1.jpg",
                        "mime_type": "image/jpeg",
                        "caption": "Portrait",
                        "source_uri": "https://example.test/image",
                    },
                }],
            })
            self.assertTrue(references.get(session_id="session-a", media_id="media_missing").local_file_missing)

    def test_registers_metadata_from_media_evidence_when_items_only_select_ids(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            media_store = FilesystemMediaStore(Path(root))
            object_key = media_store.put("media_evidence", "r1", "image/jpeg", b"image")
            references = MediaReferenceStore(media_store)
            registered = references.register_worker_result(session_id="session-a", result={
                "items": [{"media_id": "media_evidence"}],
                "evidence": [{
                    "candidate_id": "candidate-1",
                    "package_id": "wikipedia_vi",
                    "payload": {
                        "media_id": "media_evidence",
                        "object_key": object_key,
                        "mime_type": "image/jpeg",
                        "caption": "Evidence caption",
                        "source_uri": "https://example.test/media-evidence.jpg",
                    },
                }],
            })

            self.assertEqual(registered, ("media_evidence",))
            reference = references.get(session_id="session-a", media_id="media_evidence")
            self.assertEqual(reference.object_key, object_key)
            self.assertEqual(reference.caption, "Evidence caption")

    def test_allows_evidence_without_object_key_for_remote_media(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            references = MediaReferenceStore(FilesystemMediaStore(Path(root)))
            registered = references.register_worker_result(session_id="session-a", result={
                "items": [{"media_id": "remote-1"}],
                "evidence": [{
                    "candidate_id": "remote-candidate-1",
                    "package_id": "external",
                    "payload": {
                        "media_id": "remote-1",
                        "mime_type": "image/jpeg",
                        "source_uri": "https://example.test/remote.jpg",
                    },
                }],
            })

            self.assertEqual(registered, ("remote-1",))
            reference = references.get(session_id="session-a", media_id="remote-1")
            self.assertIsNone(reference.object_key)
            self.assertEqual(reference.url, "https://example.test/remote.jpg")
            self.assertFalse(reference.local_file_missing)


if __name__ == "__main__":
    unittest.main()
