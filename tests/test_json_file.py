from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kef_app.storage.json_file import write_json_atomic


class JsonFileTests(unittest.TestCase):
    def test_atomic_write_flushes_and_syncs_before_replacing_the_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            payload = {"speaker": "LS50", "volume": 37}

            with patch("kef_app.storage.json_file.os.fsync") as fsync:
                write_json_atomic(str(path), payload, prefix="test_")

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)
            fsync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
