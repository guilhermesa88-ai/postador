"""Integração local com Git real: nenhum acesso à Meta ou ao GitHub."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
import stories as s


class JournalGitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.remote = self.base / "remote.git"
        self.root = self.base / "worker"
        self.run_git(self.base, "init", "--bare", str(self.remote))
        self.run_git(self.base, "clone", str(self.remote), str(self.root))
        self.run_git(self.root, "checkout", "-b", "main")
        self.run_git(self.root, "config", "user.email", "test@example.invalid")
        self.run_git(self.root, "config", "user.name", "Local Test")
        (self.root / s.STATE).parent.mkdir()
        (self.root / s.STATE).write_text('{"schema":1,"items":{}}')
        (self.root / "feed.txt").write_text("preservar")
        self.run_git(self.root, "add", ".")
        self.run_git(self.root, "commit", "-m", "base")
        self.run_git(self.root, "push", "-u", "origin", "main")
        self.journal = s.Journal(self.root)

    @staticmethod
    def run_git(cwd, *args):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    def remote_state(self):
        return json.loads(self.run_git(self.base, "--git-dir=" + str(self.remote),
                                       "show", "main:" + s.STATE.as_posix()))

    def test_receipt_actually_pushed(self):
        state = {"schema": 1, "items": {"S1": {"status": "creating"}}}
        self.journal.save(state)
        self.assertEqual(self.remote_state(), state)
        self.assertEqual((self.root / "feed.txt").read_text(), "preservar")

    def test_rebase_preserves_parallel_feed_update(self):
        other = self.base / "other"
        self.run_git(self.base, "clone", "--branch", "main", str(self.remote), str(other))
        self.run_git(other, "config", "user.email", "test@example.invalid")
        self.run_git(other, "config", "user.name", "Other Test")
        (other / "feed.txt").write_text("reel registrado por outra execucao")
        self.run_git(other, "add", "feed.txt")
        self.run_git(other, "commit", "-m", "feed")
        self.run_git(other, "push", "origin", "main")
        state = {"schema": 1, "items": {"S1": {"status": "creating"}}}
        self.journal.save(state)
        self.assertEqual(self.remote_state(), state)
        self.assertEqual((self.root / "feed.txt").read_text(), "reel registrado por outra execucao")

    def test_remote_failure_raises(self):
        self.run_git(self.root, "remote", "set-url", "origin", str(self.base / "missing.git"))
        with self.assertRaises(s.Blocked):
            self.journal.save({"schema": 1, "items": {"S1": {"status": "creating"}}})
        self.assertEqual(self.remote_state()["items"], {})


if __name__ == "__main__":
    unittest.main()
