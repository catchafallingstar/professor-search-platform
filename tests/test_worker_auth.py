"""Offline regression tests for internal worker API secrets."""
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from services import worker_auth


class WorkerAuthTests(unittest.TestCase):
    def test_stable_token_and_file_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".worker_token"
            with patch.object(worker_auth, "_TOKEN_PATH", path):
                token = worker_auth.worker_token()
                self.assertEqual(len(token), 48)
                self.assertEqual(token, worker_auth.worker_token())
                self.assertTrue(worker_auth.check_worker_token(token))
                self.assertFalse(worker_auth.check_worker_token(""))
                self.assertFalse(worker_auth.check_worker_token("wrong"))
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_preexisting_token_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".worker_token"
            path.write_text("legacy-worker-secret", encoding="utf-8")
            with patch.object(worker_auth, "_TOKEN_PATH", path):
                self.assertEqual(worker_auth.worker_token(), "legacy-worker-secret")
                self.assertTrue(worker_auth.check_worker_token("legacy-worker-secret"))


if __name__ == "__main__":
    unittest.main()
