import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from datetime import timedelta
from PIL import Image
import stories as s
from test_stories import NOW, item, published


def sequence():
    return {**item(), "sha256": "sequence-hash", "frames": [
        {"sha256": "frame-one", "path": "01.jpg"},
        {"sha256": "frame-two", "path": "02.jpg"}]}


class SequenceTests(unittest.TestCase):
    def run_sequence(self, api=None, save_override=None):
        state, saved = {"schema": 1, "items": {}}, []
        api = api or Mock()
        api.create.side_effect = ["11", "12"]
        if not api.publish.side_effect:
            api.publish.side_effect = ["21", "22"]
        save = save_override or (lambda value: saved.append(copy.deepcopy(value)))
        return state, saved, api, save

    def test_order_and_each_frame_has_durable_intent(self):
        state, saved, api, save = self.run_sequence()
        def create(url):
            frame = saved[-1]["items"]["EV-S001"]["frames"][-1]
            self.assertEqual(frame["status"], "creating")
            self.assertEqual(frame["image_url"], url)
            return str(10 + frame["index"])
        api.create.side_effect = create
        s.publish_sequence(sequence(), state, api, save, ["url1", "url2"], NOW)
        self.assertEqual([c.args[0] for c in api.create.call_args_list], ["url1", "url2"])
        self.assertEqual([c.args[0] for c in api.publish.call_args_list], ["11", "12"])
        receipt = state["items"]["EV-S001"]
        self.assertEqual(receipt["status"], "published")
        self.assertEqual([f["media_id"] for f in receipt["frames"]], ["21", "22"])

    def test_failure_on_second_does_not_repeat_first_or_continue(self):
        api = Mock()
        api.publish.side_effect = ["21", s.Blocked("timeout")]
        state, saved, api, save = self.run_sequence(api)
        with self.assertRaises(s.Blocked):
            s.publish_sequence(sequence(), state, api, save, ["url1", "url2"], NOW)
        frames = saved[-1]["items"]["EV-S001"]["frames"]
        self.assertEqual(frames[0]["status"], "published")
        self.assertEqual(frames[1]["status"], "publishing")
        with self.assertRaises(s.Blocked):
            s.select([sequence()], saved[-1], NOW, 8)
        with self.assertRaises(s.Blocked):
            s.publish_sequence(sequence(), state, api, save, ["url1", "url2"], NOW)
        self.assertEqual(api.publish.call_count, 2)

    def test_initial_save_failure_prevents_post(self):
        state, saved, api, save = self.run_sequence(save_override=Mock(side_effect=s.Blocked("disk")))
        with self.assertRaises(s.Blocked):
            s.publish_sequence(sequence(), state, api, save, ["url1", "url2"], NOW)
        api.create.assert_not_called()

    def test_final_save_failure_blocks_following_sequences(self):
        state, saved, api, save = self.run_sequence()
        def fail_final(value):
            if value["items"]["EV-S001"]["status"] == "published":
                raise s.Blocked("push")
            save(value)
        with self.assertRaises(s.Blocked):
            s.publish_sequence(sequence(), state, api, fail_final, ["url1", "url2"], NOW)
        with self.assertRaises(s.Blocked):
            s.select([sequence()], saved[-1], NOW, 8)
        self.assertEqual(api.publish.call_count, 2)

    def test_eight_sequences_not_eight_images(self):
        old = {**published(), "sha256": "old", "frames": [{"sha256": "other"}] * 10}
        state = {"items": {str(i): old for i in range(7)}}
        self.assertIsNotNone(s.select([sequence()], state, NOW, 8))
        state["items"]["8"] = old
        self.assertIsNone(s.select([sequence()], state, NOW, 8))

    def test_old_single_image_receipt_is_preserved_and_counts(self):
        old = published()
        state = {"items": {"EV-S001": old}}
        before = copy.deepcopy(state)
        self.assertIsNone(s.select([item()], state, NOW, 8))
        self.assertEqual(state, before)

    def test_frame_cannot_be_reused_from_earlier_sequence(self):
        old = {**published(), "frames": [{"sha256": "frame-one"}]}
        self.assertIsNone(s.select([sequence()], {"items": {"old": old}}, NOW, 8))

    def test_quota_checks_whole_sequence_before_start(self):
        api = s.Meta("123", "test-secret")
        api.request = Mock(side_effect=[{"username": "estilovidya", "account_type": "BUSINESS"},
            {"data": [{"quota_usage": 87, "config": {"quota_total": 100}}]}])
        with self.assertRaises(s.Blocked):
            api.check(required=10)
        self.assertTrue(all(c.args[0] == "GET" for c in api.request.call_args_list))

    def test_midnight_count_uses_brasilia_start_day(self):
        old = {**published(), "sha256": "other", "started_at": "2026-09-17T02:59:00+00:00",
               "published_at": "2026-09-17T03:01:00+00:00"}
        next_day = s.date("2026-09-17T12:00:00+00:00")
        self.assertIsNotNone(s.select([sequence()], {"items": {"old": old}}, next_day, 1))


class SequenceManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / s.QUEUE / "EV-S001"
        self.folder.mkdir(parents=True)
        frames = []
        for i, color in enumerate(("white", "black"), 1):
            buf = io.BytesIO()
            Image.new("RGB", (1080, 1920), color).save(buf, "JPEG")
            name = f"{i:02d}.jpg"
            (self.folder / name).write_bytes(buf.getvalue())
            frames.append({"file": name, "sha256": hashlib.sha256(buf.getvalue()).hexdigest()})
        self.manifest = {**item(), "images": frames,
            "sha256": hashlib.sha256("\n".join(f["sha256"] for f in frames).encode()).hexdigest()}

    def load(self):
        (self.folder / "story.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        return s.load_queue(self.root)

    def test_order_loaded(self):
        self.assertTrue(self.load()[0]["frames"][0]["path"].endswith("01.jpg"))

    def test_reordered_images_invalidate_approval(self):
        self.manifest["images"].reverse()
        with self.assertRaises(s.Blocked):
            self.load()

    def test_changed_second_image_blocks_entire_sequence(self):
        (self.folder / "02.jpg").write_bytes((self.folder / "01.jpg").read_bytes())
        with self.assertRaises(s.Blocked):
            self.load()

    def test_path_traversal_rejected(self):
        self.manifest["images"][0]["file"] = "../escape.jpg"
        with self.assertRaises(s.Blocked):
            self.load()

    def test_repeated_frame_rejected(self):
        self.manifest["images"][1] = self.manifest["images"][0]
        with self.assertRaises(s.Blocked):
            self.load()


if __name__ == "__main__":
    unittest.main()
