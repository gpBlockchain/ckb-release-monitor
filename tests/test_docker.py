import contextlib
import copy
import datetime as dt
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

spec = importlib.util.spec_from_file_location("check_docker", Path(__file__).resolve().parents[1] / "scripts/check-docker.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
NOW = dt.datetime(2026, 11, 23, tzinfo=dt.timezone.utc)
RELEASE = {"tag_name": "v0.210.0", "published_at": "2026-11-01T00:00:00Z", "draft": False, "prerelease": False}
IMAGE = "application/vnd.docker.distribution.manifest.v2+json"
INDEX = "application/vnd.docker.distribution.manifest.list.v2+json"


class Fixture:
    repository = "nervos/ckb"

    def __init__(self):
        self.refs = {}
        self.publish("v0.210.0")

    def add(self, obj, tag=None):
        raw = json.dumps(obj, sort_keys=True).encode()
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        self.refs[digest] = (digest, obj)
        if tag:
            self.refs[tag] = (digest, obj)
        return {"mediaType": obj["mediaType"], "digest": digest, "size": len(raw)}

    def publish(self, tag, salt=""):
        children = []
        for arch, prefix in (("amd64", "x64-"), ("arm64", "aarch64-")):
            blob = {"mediaType": "application/octet-stream", "size": 1,
                    "digest": "sha256:" + hashlib.sha256((arch + salt).encode()).hexdigest()}
            desc = self.add({"schemaVersion": 2, "mediaType": IMAGE,
                             "config": blob, "layers": [blob]}, prefix + tag)
            desc["platform"] = {"os": "linux", "architecture": arch}
            children.append(desc)
        self.add({"schemaVersion": 2, "mediaType": INDEX, "manifests": children}, tag)
        self.refs["latest"] = self.refs[tag]

    def manifest(self, ref):
        if ref not in self.refs:
            raise FileNotFoundError("HTTP 404 missing: " + ref)
        return self.refs[ref]


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = Fixture()
        self.releases = [copy.deepcopy(RELEASE)]

    def run_check(self):
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            status = m.monitor(self.root, self.releases, self.registry, now=NOW)
        return status, stream.getvalue()

    def baseline(self):
        return self.root / "baseline/docker/v0.210.0.json"

    def test_new_then_unchanged(self):
        status, output = self.run_check()
        self.assertEqual(status, 0)
        self.assertIn("NEW docker-v0.210.0", output)
        self.assertEqual((self.root / "out/new.txt").read_text(), "docker-v0.210.0\n")
        status, output = self.run_check()
        self.assertEqual(status, 0)
        self.assertIn("OK docker-v0.210.0", output)

    def test_overwrite_detected_baseline_preserved(self):
        self.run_check()
        original = self.baseline().read_bytes()
        self.registry.publish("v0.210.0", salt="tampered")
        status, output = self.run_check()
        self.assertEqual(status, 1)
        self.assertIn("descriptors changed", output)
        self.assertEqual(self.baseline().read_bytes(), original)
        self.assertTrue((self.root / "out/docker-v0.210.0.diff").is_file())

    def test_missing_architecture_no_new_baseline(self):
        del self.registry.refs["aarch64-v0.210.0"]
        self.assertEqual(self.run_check()[0], 1)
        self.assertFalse(self.baseline().exists())

    def test_missing_version_detected_after_baseline(self):
        self.run_check()
        del self.registry.refs["v0.210.0"]
        self.assertEqual(self.run_check()[0], 1)

    def test_architecture_alias_mismatch(self):
        self.registry.refs["aarch64-v0.210.0"] = self.registry.refs["x64-v0.210.0"]
        self.assertEqual(self.run_check()[0], 1)
        self.assertFalse(self.baseline().exists())

    def test_latest_retargeted(self):
        self.registry.publish("v0.209.0", salt="other")
        self.assertEqual(self.run_check()[0], 1)
        self.assertIn("docker-latest", (self.root / "out/alert.txt").read_text())

    def test_latest_platform_metadata_tampered(self):
        _, index = copy.deepcopy(self.registry.refs["latest"])
        index["manifests"][0]["platform"]["os"] = "windows"
        self.registry.add(index, "latest")
        self.assertEqual(self.run_check()[0], 1)

    def test_legitimate_latest_upgrade(self):
        self.run_check()
        self.registry.publish("v0.211.0")
        self.releases.append(dict(RELEASE, tag_name="v0.211.0", published_at="2026-11-20T00:00:00Z"))
        status, output = self.run_check()
        self.assertEqual(status, 0)
        self.assertIn("OK docker-latest -> v0.211.0", output)

    def test_removed_release_kept_under_watch(self):
        self.run_check()
        self.releases = []
        self.registry.publish("v0.210.0", salt="tampered")
        status, output = self.run_check()
        self.assertEqual(status, 1)
        self.assertIn("DOCKER docker-v0.210.0", output)

    def test_old_approved_version_kept_under_watch(self):
        self.run_check()
        self.releases[0]["published_at"] = "2020-01-01T00:00:00Z"
        self.registry.publish("v0.210.0", salt="tampered")
        self.assertEqual(self.run_check()[0], 1)

    def test_existing_release_alert_not_erased(self):
        out = self.root / "out"
        out.mkdir()
        (out / "alert.txt").write_text("CHANGED v0.210.0\n")
        self.run_check()
        self.assertEqual((out / "alert.txt").read_text(), "CHANGED v0.210.0\n")

    def test_request_failure_is_alert(self):
        with patch.object(self.registry, "manifest", side_effect=TimeoutError("timeout")):
            self.assertEqual(self.run_check()[0], 1)
        self.assertFalse(self.baseline().exists())

    def test_unknown_version_format_is_alert(self):
        self.releases[0]["tag_name"] = "../unsafe"
        self.assertEqual(self.run_check()[0], 1)
        self.assertFalse(self.baseline().exists())


class CommittedBaselineTests(unittest.TestCase):
    def test_committed_snapshots_have_valid_schema(self):
        directory = Path(__file__).resolve().parents[1] / "baseline/docker"
        files = sorted(directory.glob("*.json"))
        self.assertTrue(files, "initial Docker snapshots must be committed")
        for path in files:
            with self.subTest(tag=path.stem):
                data = json.loads(path.read_text())
                self.assertIsNotNone(m.VERSION.fullmatch(path.stem))
                self.assertEqual(data["tag"], path.stem)
                self.assertEqual(data["repository"], "nervos/ckb")
                self.assertIsNotNone(m.DIGEST.fullmatch(data["digest"]))
                self.assertEqual(set(data["platforms"]), {"linux/amd64", "linux/arm64"})
                for image in data["platforms"].values():
                    self.assertIsNotNone(m.DIGEST.fullmatch(image["digest"]))
                    m.descriptor(image["config"])
                    self.assertTrue(image["layers"])
                    for layer in image["layers"]:
                        m.descriptor(layer)


class TransportTests(unittest.TestCase):
    def registry(self):
        r = object.__new__(m.Registry)
        r.repository, r.token, r.cache = "nervos/ckb", "fixture", {}
        return r

    def test_header_digest_is_not_trusted(self):
        raw = json.dumps({"schemaVersion": 2, "mediaType": IMAGE}).encode()
        with patch.object(m, "request", return_value=(raw, {"Docker-Content-Digest": "sha256:" + "0" * 64})):
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                self.registry().manifest("v0.210.0")

    def test_requested_child_digest_verified_without_header(self):
        with patch.object(m, "request", return_value=(b'{}', {})):
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                self.registry().manifest("sha256:" + "0" * 64)

    def test_correct_digest(self):
        raw = json.dumps({"schemaVersion": 2, "mediaType": IMAGE}).encode()
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        with patch.object(m, "request", return_value=(raw, {"Docker-Content-Digest": digest})):
            self.assertEqual(self.registry().manifest(digest)[0], digest)

    def test_bad_schema_rejected(self):
        with patch.object(m, "request", return_value=(b'{"schemaVersion": 1}', {})):
            with self.assertRaisesRegex(ValueError, "schema"):
                self.registry().manifest("tag")


if __name__ == "__main__":
    unittest.main()
