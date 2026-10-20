"""A training ZIP's entry sizes are checked before anything is inflated
(ov-review D-03).

/train/upload-bundle capped the request (500 MB) and the entry count, then
called zf.read() on each entry - which inflates the WHOLE entry in memory -
and only afterwards compared the caption to its 64 KB cap; images had no
inflated-size cap at all. A small archive declaring a multi-GB entry took the
panel down. The declared size is now checked first and the entry skipped.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

routes_train = sys.modules["panel.routes_train"]


class _Field:
    def __init__(self, name, data):
        self.filename = name
        self.file = io.BytesIO(data)


class _Form(dict):
    def getvalue(self, k):
        return self.get(k)


class _H:
    def __init__(self, n):
        self.headers = {"Content-Length": str(n)}
        self.rfile = None
        self.status = None
        self.payload = None

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status

    def send_error(self, code, *a):
        self.status = code


class BundleEntrySizes(unittest.TestCase):
    def test_oversized_entries_are_skipped_unread(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("big.png", b"\0" * (P.TRAIN_MAX_BYTES_PER_IMAGE + 1))
            zf.writestr("big.txt", b"a" * (P.TRAIN_CAPTION_MAX_BYTES + 1))
        data = buf.getvalue()
        state = Path(tempfile.mkdtemp(prefix="bundle-"))
        form = _Form(job_id="trn_bundle_t", file=_Field("set.zip", data))
        read_names: list[str] = []
        real_read = zipfile.ZipFile.read

        def spy(self, name, *a, **k):
            read_names.append(getattr(name, "filename", name))
            return real_read(self, name, *a, **k)

        h = _H(len(data))
        with mock.patch.object(P, "_parse_multipart_form", lambda *a, **k: form), \
             mock.patch.object(P, "_train_dataset_dir", lambda jid: state / jid), \
             mock.patch.object(zipfile.ZipFile, "read", spy):
            routes_train.post_train_upload_bundle(h, "/train/upload-bundle", {},
                                                  "multipart/form-data; boundary=x")
        self.assertEqual(read_names, [])
        self.assertEqual(list((state / "trn_bundle_t" / "images").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
