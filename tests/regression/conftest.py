"""
Shared fixtures and utilities for tap-helpscout regression tests.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REGRESSION_DIR = Path(__file__).parent
BASELINE_DIR = REGRESSION_DIR / "baseline"
ROTATED_TOKEN_FILE = BASELINE_DIR / ".refresh_token"
TAP_CMD = str(Path(sys.executable).parent / "tap-helpscout")

REQUIRED_ENV = [
    "TAP_HELPSCOUT_CLIENT_ID",
    "TAP_HELPSCOUT_CLIENT_SECRET",
    "TAP_HELPSCOUT_REFRESH_TOKEN",
]

# Lightweight streams: low record count, good for regression
DEFAULT_STREAMS = ["mailboxes", "users", "teams"]


def _refresh_token():
    """Prefer the rotated token saved by capture.py; fall back to env var."""
    if ROTATED_TOKEN_FILE.exists():
        tok = ROTATED_TOKEN_FILE.read_text().strip()
        if tok:
            return tok
    return os.getenv("TAP_HELPSCOUT_REFRESH_TOKEN", "")


def build_config():
    missing = [v for v in REQUIRED_ENV if not os.getenv(v)]
    if missing:
        pytest.skip(f"Missing required env vars: {missing}")

    return {
        "client_id": os.environ["TAP_HELPSCOUT_CLIENT_ID"],
        "client_secret": os.environ["TAP_HELPSCOUT_CLIENT_SECRET"],
        "refresh_token": _refresh_token(),
        "user_agent": "peliqan-regression/1.0",
        "start_date": os.getenv("TAP_HELPSCOUT_START_DATE", "2024-01-01T00:00:00Z"),
    }


def _tap_env():
    env = os.environ.copy()
    env.setdefault("AES_SECRET_KEY", "peliqan-test-key")
    return env


def discover_catalog(config_path):
    result = subprocess.run(
        [TAP_CMD, "--config", config_path, "--discover"],
        capture_output=True, text=True, env=_tap_env()
    )
    if result.returncode != 0:
        raise RuntimeError(f"discover failed: {result.stderr[-2000:]}")
    return json.loads(result.stdout)


def select_streams(catalog, only_streams):
    only = set(only_streams)
    for stream in catalog.get("streams", []):
        sid = stream.get("tap_stream_id") or stream.get("stream")
        selected = sid in only
        for entry in stream.get("metadata", []):
            if entry.get("breadcrumb") == []:
                entry.setdefault("metadata", {})["selected"] = selected
            else:
                if selected and entry["metadata"].get("inclusion") != "unsupported":
                    entry["metadata"]["inclusion"] = "automatic"
    return catalog


def write_catalog(catalog):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(catalog, tmp)
    tmp.close()
    return tmp.name


@pytest.fixture(scope="session")
def config_file():
    """Writable config file — tap rotates refresh_token on each call."""
    config = build_config()
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(config, tmp)
    tmp.close()
    yield tmp.name
    # Persist the rotated refresh_token so subsequent runs work
    try:
        with open(tmp.name) as f:
            final = json.load(f)
        new_tok = final.get("refresh_token")
        if new_tok:
            BASELINE_DIR.mkdir(parents=True, exist_ok=True)
            ROTATED_TOKEN_FILE.write_text(new_tok)
    except Exception:
        pass
    os.unlink(tmp.name)


@pytest.fixture(scope="session")
def include_streams():
    raw = os.getenv("TAP_HELPSCOUT_INCLUDE_STREAMS", ",".join(DEFAULT_STREAMS))
    return [s.strip() for s in raw.split(",") if s.strip()]


@pytest.fixture(scope="session")
def catalog_file(config_file, include_streams):
    # Keep all streams in the catalog (the tap looks up children by name)
    # but only mark our target streams as selected.
    catalog = discover_catalog(config_file)
    select_streams(catalog, include_streams)
    path = write_catalog(catalog)
    yield path
    os.unlink(path)


def run_tap(config_path, catalog_path=None, extra_args=None):
    cmd = [TAP_CMD, "--config", config_path]
    if catalog_path:
        cmd += ["--catalog", catalog_path]
    cmd += (extra_args or [])
    result = subprocess.run(cmd, capture_output=True, text=True, env=_tap_env())
    return result.stdout, result.stderr, result.returncode


def parse_messages(stdout):
    schemas, records, states = {}, {}, []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = msg.get("type")
        if t == "SCHEMA":
            schemas[msg["stream"]] = msg["schema"]
            records.setdefault(msg["stream"], [])
        elif t == "RECORD":
            records.setdefault(msg["stream"], []).append(msg["record"])
        elif t == "STATE":
            states.append(msg["value"])
    return schemas, records, states
