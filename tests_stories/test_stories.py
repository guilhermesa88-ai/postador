import copy
import hashlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import URLError, HTTPError
from PIL import Image
import stories as s

NOW = datetime(2026, 9, 16, 15, tzinfo=timezone.utc)


def jpeg():
    out = io.BytesIO()
    Image.new("RGB", (1080, 1920), "#fcf5e8").save(out, "JPEG")
    return out.getvalue()


def item():
    return {"id": "EV-S001", "approved": True, "approved_by": "teste-local",
            "approved_at": (NOW - timedelta(hours=1)).isoformat(),
            "publish_after": (NOW - timedelta(hours=1)).isoformat(),
            "publish_before": (NOW + timedelta(days=1)).isoformat(),
            "sha256": hashlib.sha256(jpeg()).hexdigest(),
            "path": "entrada-stories/estilovidya/EV-S001/arte.jpg"}


def published():
    return {"status": "published", "published_at": NOW.isoformat(),
            "sha256": item()["sha256"]}


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / s.QUEUE / "EV-S001"
        self.folder.mkdir(parents=True)
        (self.folder / "arte.jpg").write_bytes(jpeg())

    def write(self, **overrides):
        data = {**item(), **overrides}
        (self.folder / "story.json").write_text(json.dumps(data), encoding="utf-8")

    def test_valid_image_and_queue(self):
        self.write()
        self.assertEqual(s.load_queue(self.root)[0]["id"], "EV-S001")

    def test_unapproved_ignored(self):
        self.write(approved=False)
        self.assertEqual(s.load_queue(self.root), [])

    def test_changed_image_blocks(self):
        self.write(sha256="0" * 64)
        with self.assertRaises(s.Blocked):
            s.load_queue(self.root)

    def test_approval_requires_author(self):
        self.write(approved_by="")
        with self.assertRaises(s.Blocked):
            s.load_queue(self.root)

    def test_invalid_id_blocks(self):
        self.write(id="../escape")
        with self.assertRaises(s.Blocked):
            s.load_queue(self.root)

    def test_timezone_required(self):
        with self.assertRaises(s.Blocked):
            s.date("2026-09-16T10:00:00")

    def test_wrong_dimensions(self):
        out = io.BytesIO()
        Image.new("RGB", (100, 100)).save(out, "JPEG")
        with self.assertRaises(s.Blocked):
            s.validate_image(out.getvalue())

    def test_corrupt_image(self):
        with self.assertRaises(s.Blocked):
            s.validate_image(jpeg()[:100])

    def test_reversed_window(self):
        self.write(publish_before=(NOW - timedelta(days=2)).isoformat())
        with self.assertRaises(s.Blocked):
            s.load_queue(self.root)


class SelectionTests(unittest.TestCase):
    def test_due(self):
        self.assertEqual(s.select([item()], {"items": {}}, NOW, 3)["id"], "EV-S001")

    def test_future(self):
        future = {**item(), "publish_after": (NOW + timedelta(hours=1)).isoformat()}
        self.assertIsNone(s.select([future], {"items": {}}, NOW, 3))

    def test_expired(self):
        expired = {**item(), "publish_before": NOW.isoformat()}
        self.assertIsNone(s.select([expired], {"items": {}}, NOW, 3))

    def test_published_id_skipped(self):
        self.assertIsNone(s.select([item()], {"items": {"EV-S001": published()}}, NOW, 3))

    def test_duplicate_content_skipped(self):
        self.assertIsNone(s.select([item()], {"items": {"old-id": published()}}, NOW, 3))

    def test_pending_blocks_entire_queue(self):
        for status in ("creating", "processing", "publishing"):
            with self.subTest(status=status), self.assertRaises(s.Blocked):
                s.select([item()], {"items": {"old": {"status": status}}}, NOW, 3)

    def test_daily_limit(self):
        old = {**published(), "sha256": "different"}
        self.assertIsNone(s.select([item()], {"items": {"old": old}}, NOW, 1))

    def test_brasilia_date_not_utc(self):
        # 01 UTC do dia 17 ainda pertence ao dia 16 em Brasilia.
        later = datetime(2026, 9, 17, 1, tzinfo=timezone.utc)
        old = {**published(), "sha256": "different"}
        self.assertIsNone(s.select([item()], {"items": {"old": old}}, later, 1))


class MetaTests(unittest.TestCase):
    def api(self):
        return s.Meta("123", "secret-value")

    def test_story_payload(self):
        api = self.api()
        api.request = Mock(return_value={"id": "456"})
        self.assertEqual(api.create("https://example.org/a.jpg"), "456")
        api.request.assert_called_once_with("POST", "123/media",
                    media_type="STORIES", image_url="https://example.org/a.jpg")

    def test_wrong_account(self):
        api = self.api()
        api.request = Mock(return_value={"username": "other", "account_type": "BUSINESS"})
        with self.assertRaises(s.Blocked):
            api.check()

    def test_nonbusiness(self):
        api = self.api()
        api.request = Mock(return_value={"username": "estilovidya", "account_type": "MEDIA_CREATOR"})
        with self.assertRaises(s.Blocked):
            api.check()

    def test_quota_missing_closed(self):
        api = self.api()
        api.request = Mock(side_effect=[{"username": "estilovidya", "account_type": "BUSINESS"}, {}])
        with self.assertRaises(s.Blocked):
            api.check()

    def test_preflight_success(self):
        api = self.api()
        api.request = Mock(side_effect=[{"username": "estilovidya", "account_type": "BUSINESS"},
            {"data": [{"quota_usage": 4, "config": {"quota_total": 100}}]}])
        api.check()

    def test_quota_headroom(self):
        api = self.api()
        api.request = Mock(side_effect=[{"username": "estilovidya", "account_type": "BUSINESS"},
            {"data": [{"quota_usage": 95, "config": {"quota_total": 100}}]}])
        with self.assertRaises(s.Blocked):
            api.check()

    def test_no_post_retry_or_token_leak(self):
        opener = Mock(side_effect=URLError("secret-value"))
        api = s.Meta("123", "secret-value", opener=opener)
        with self.assertRaises(s.Blocked) as caught:
            api.publish("456")
        opener.assert_called_once()
        self.assertNotIn("secret-value", str(caught.exception))
        request = opener.call_args.args[0]
        self.assertNotIn("secret-value", request.full_url)

    def test_http_500_no_retry(self):
        opener = Mock(side_effect=HTTPError("url", 500, "secret-value", {}, None))
        api = s.Meta("123", "secret-value", opener=opener)
        with self.assertRaises(s.Blocked):
            api.create("https://example.org/a.jpg")
        opener.assert_called_once()

    def test_missing_id(self):
        api = self.api()
        api.request = Mock(return_value={})
        with self.assertRaises(s.Blocked):
            api.publish("456")

    def test_processing_finished(self):
        api = self.api()
        api.request = Mock(side_effect=[{"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"}])
        with patch("stories.time.sleep"):
            api.ready("456")

    def test_processing_error(self):
        api = self.api()
        api.request = Mock(return_value={"status_code": "ERROR"})
        with self.assertRaises(s.Blocked):
            api.ready("456")


class PublicationTests(unittest.TestCase):
    def setup_run(self):
        api = Mock()
        api.create.return_value = "456"
        api.publish.return_value = "789"
        state = {"schema": 1, "items": {}}
        saved = []
        def save(value):
            saved.append(copy.deepcopy(value))
        return api, state, saved, save

    def test_complete_receipt_sequence(self):
        api, state, saved, save = self.setup_run()
        s.publish_one(item(), state, api, save, "https://example.org/a.jpg", NOW)
        self.assertEqual([x["items"]["EV-S001"]["status"] for x in saved],
                         ["creating", "processing", "publishing", "published"])
        self.assertEqual(saved[-1]["items"]["EV-S001"]["media_id"], "789")
        api.create.assert_called_once()
        api.publish.assert_called_once()

    def test_intent_must_persist_before_any_post(self):
        api, state, saved, save = self.setup_run()
        with self.assertRaises(s.Blocked):
            s.publish_one(item(), state, api, Mock(side_effect=s.Blocked("disk")),
                          "https://example.org/a.jpg", NOW)
        api.create.assert_not_called()
        api.publish.assert_not_called()

    def test_timeout_publishing_never_requeued(self):
        api, state, saved, save = self.setup_run()
        api.publish.side_effect = s.Blocked("timeout")
        with self.assertRaises(s.Blocked):
            s.publish_one(item(), state, api, save, "https://example.org/a.jpg", NOW)
        self.assertEqual(saved[-1]["items"]["EV-S001"]["status"], "publishing")
        with self.assertRaises(s.Blocked):
            s.select([item()], saved[-1], NOW, 3)
        api.publish.assert_called_once()

    def test_failure_saving_final_receipt_blocks_rerun(self):
        api, state, saved, save = self.setup_run()
        def fail_final(value):
            if value["items"]["EV-S001"]["status"] == "published":
                raise s.Blocked("push failed")
            save(value)
        with self.assertRaises(s.Blocked):
            s.publish_one(item(), state, api, fail_final, "https://example.org/a.jpg", NOW)
        with self.assertRaises(s.Blocked):
            s.select([item()], saved[-1], NOW, 3)

    def test_create_failure_blocks_rerun(self):
        api, state, saved, save = self.setup_run()
        api.create.side_effect = s.Blocked("timeout")
        with self.assertRaises(s.Blocked):
            s.publish_one(item(), state, api, save, "https://example.org/a.jpg", NOW)
        self.assertEqual(saved[-1]["items"]["EV-S001"]["status"], "creating")
        api.publish.assert_not_called()

    def test_media_immutable_commit_url(self):
        url = s.media_url(Path("."), item(), "a" * 40)
        self.assertIn("/" + "a" * 40 + "/", url)
        self.assertNotIn("/main/", url)

    def test_live_blocked_outside_actions(self):
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "false"}), self.assertRaises(s.Blocked):
            s.Journal(Path(".")).check()

    def test_remote_wrong_hash(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock()
        response.read.return_value = b"changed"
        with patch("stories.urlopen", return_value=response), self.assertRaises(s.Blocked):
            s.check_remote("https://example.org/a.jpg", item()["sha256"])


if __name__ == "__main__":
    unittest.main()
