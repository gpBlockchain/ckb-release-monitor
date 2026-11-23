#!/usr/bin/env python3
"""Check Docker Hub tag integrity without executing untrusted images (stdlib only)."""
import datetime as dt
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-rc[0-9]+)?\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
ACCEPT = ", ".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))


def request(url, headers=None):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=45) as r:
                return r.read(), r.headers
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


class Registry:
    def __init__(self, repository="nervos/ckb"):
        # Intentionally restricted to Docker Hub; never send GH_TOKEN to the registry.
        if not re.fullmatch(r"[a-z0-9._-]+/[a-z0-9._-]+", repository):
            raise ValueError("invalid Docker Hub repository")
        self.repository = repository
        query = urllib.parse.urlencode({"service": "registry.docker.io", "scope": f"repository:{repository}:pull"})
        raw, _ = request("https://auth.docker.io/token?" + query)
        self.token = json.loads(raw)["token"]
        self.cache = {}

    def manifest(self, reference):
        if reference not in self.cache:
            raw, headers = request(
                f"https://registry-1.docker.io/v2/{self.repository}/manifests/{reference}",
                {"Accept": ACCEPT, "Authorization": "Bearer " + self.token},
            )
            actual = "sha256:" + hashlib.sha256(raw).hexdigest()
            claimed = headers.get("Docker-Content-Digest")
            if (claimed and actual != claimed) or (reference.startswith("sha256:") and actual != reference):
                raise ValueError("manifest content digest mismatch")
            obj = json.loads(raw)
            if obj.get("schemaVersion") != 2 or obj.get("mediaType") not in ACCEPT.split(", "):
                raise ValueError("unsupported manifest schema/mediaType")
            self.cache[reference] = (actual, obj)
            self.cache[actual] = (actual, obj)
        return self.cache[reference]


def descriptor(value):
    if not DIGEST.fullmatch(value["digest"]) or not isinstance(value["size"], int) or value["size"] < 0:
        raise ValueError("invalid content descriptor")
    return {key: value[key] for key in ("mediaType", "digest", "size")}


def image(registry, reference):
    digest, obj = registry.manifest(reference)
    if "manifests" in obj:
        raise ValueError("expected single-platform image")
    return {"digest": digest, "config": descriptor(obj["config"]),
            "layers": [descriptor(layer) for layer in obj["layers"]]}


def index_snapshot(registry, tag):
    root, index = registry.manifest(tag)
    if not isinstance(index.get("manifests"), list) or not index["manifests"]:
        raise ValueError("expected nonempty multi-platform manifest index")
    platforms = {}
    for child in index["manifests"]:
        descriptor(child)
        p = child["platform"]
        key = "/".join(filter(None, (p["os"], p["architecture"], p.get("variant"))))
        if key in platforms:
            raise ValueError("duplicate platform: " + key)
        platforms[key] = image(registry, child["digest"])
    return {"repository": registry.repository, "tag": tag, "digest": root,
            "platforms": platforms}


def snapshot(registry, tag):
    result = index_snapshot(registry, tag)
    platforms = result["platforms"]
    for suffix, arch in (("x64-", "amd64"), ("aarch64-", "arm64")):
        alias = image(registry, suffix + tag)
        matching = [v for k, v in platforms.items() if k == "linux/" + arch or k.startswith("linux/" + arch + "/")]
        if len(matching) != 1 or matching[0] != alias:
            raise ValueError("architecture tag mismatch: " + suffix + tag)
    if len(platforms) != 2:
        raise ValueError("unexpected platform set")
    return result


def github_releases(upstream):
    raw = subprocess.check_output(["gh", "api", "--paginate", "--slurp",
                                   f"repos/{upstream}/releases?per_page=100"], text=True)
    return [r for page in json.loads(raw) for r in page if not r["draft"]]


def monitor(root, releases, registry, days=183, now=None):
    """Return 1 on drift OR incomplete checks. Never overwrite approved baselines."""
    cutoff = (now or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=days)
    out = root / "out"
    base = root / "baseline" / "docker"
    out.mkdir(exist_ok=True)
    base.mkdir(parents=True, exist_ok=True)
    alerts = []

    def alert(key, message):
        # Second field is the existing workflow's per-issue grouping key.
        line = f"DOCKER {key} {message}"
        alerts.append(line)
        print(line)
        with (out / "alert.txt").open("a") as f:
            f.write(line + "\n")

    selected = {r["tag_name"] for r in releases
                if dt.datetime.fromisoformat(r["published_at"].replace("Z", "+00:00")) >= cutoff}
    # Keep checking every already approved Docker tag, even after the release
    # disappears or ages out. The 183-day window limits only initial discovery.
    selected.update(p.stem for p in base.glob("*.json"))
    stable = [r for r in releases if not r["prerelease"] and VERSION.fullmatch(r["tag_name"]) and "-rc" not in r["tag_name"]]
    latest = max(stable, key=lambda r: r["published_at"])["tag_name"] if stable else None
    if latest:
        selected.add(latest)
    observed = {}
    for tag in sorted(selected):
        key = "docker-" + tag
        if not VERSION.fullmatch(tag):
            alert("docker-discovery", "unsupported release tag " + repr(tag))
            continue
        try:
            current = snapshot(registry, tag)
            observed[tag] = current
            text = json.dumps(current, sort_keys=True, indent=2) + "\n"
            path = base / (tag + ".json")
            if not path.exists():
                path.write_text(text)
                with (out / "new.txt").open("a") as f:
                    f.write(key + "\n")
                print("NEW " + key + " (manual review required)")
            elif path.read_text() != text:
                delta = "".join(difflib.unified_diff(path.read_text().splitlines(True), text.splitlines(True),
                                                   fromfile=str(path), tofile="observed/" + tag))
                (out / (key + ".diff")).write_text(delta)
                alert(key, "manifest/config/layer descriptors changed")
            else:
                print("OK " + key)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            alert(key, f"check failed: {type(e).__name__}: {e}")
    try:
        if latest is None or latest not in observed:
            raise ValueError("latest stable release not successfully inspected")
        # Floating latest may legitimately change; compare its child image set
        # against the current stable version, not yesterday's latest digest.
        current_latest = index_snapshot(registry, "latest")
        if current_latest["platforms"] != observed[latest]["platforms"]:
            raise ValueError("latest does not match stable " + latest)
        print("OK docker-latest -> " + latest)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        alert("docker-latest", f"check failed: {type(e).__name__}: {e}")
    return int(bool(alerts))


def main():
    root = Path(__file__).resolve().parent.parent
    (root / "out").mkdir(exist_ok=True)
    try:
        return monitor(root, github_releases(os.getenv("UPSTREAM", "nervosnetwork/ckb")),
                       Registry(os.getenv("DOCKER_REPOSITORY", "nervos/ckb")),
                       int(os.getenv("MONITOR_DAYS", "183")))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError) as e:
        line = f"DOCKER docker-discovery check failed: {type(e).__name__}: {e}"
        print(line, file=sys.stderr)
        with (root / "out" / "alert.txt").open("a") as f:
            f.write(line + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
