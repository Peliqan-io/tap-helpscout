#!/usr/bin/env python3
"""
Baseline capture for tap-helpscout — run once on Python 3.9.

Required env vars:
    TAP_HELPSCOUT_CLIENT_ID
    TAP_HELPSCOUT_CLIENT_SECRET
    TAP_HELPSCOUT_REFRESH_TOKEN
    TAP_HELPSCOUT_INCLUDE_STREAMS   (default "mailboxes,users,teams")
    TAP_HELPSCOUT_START_DATE        (default 2024-01-01T00:00:00Z)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BASELINE_DIR = Path(__file__).parent / "baseline"
ROTATED_TOKEN_FILE = BASELINE_DIR / ".refresh_token"
TAP_CMD = str(Path(sys.executable).parent / "tap-helpscout")

REQUIRED = [
    "TAP_HELPSCOUT_CLIENT_ID",
    "TAP_HELPSCOUT_CLIENT_SECRET",
    "TAP_HELPSCOUT_REFRESH_TOKEN",
]


def check_env():
    missing = [v for v in REQUIRED if not os.getenv(v)]
    if missing:
        print(f"ERROR: Missing env vars: {missing}")
        sys.exit(1)


def _refresh_token():
    """Prefer the rotated token from a previous run; fall back to env var."""
    if ROTATED_TOKEN_FILE.exists():
        tok = ROTATED_TOKEN_FILE.read_text().strip()
        if tok:
            return tok
    return os.environ["TAP_HELPSCOUT_REFRESH_TOKEN"]


def write_config():
    config = {
        "client_id": os.environ["TAP_HELPSCOUT_CLIENT_ID"],
        "client_secret": os.environ["TAP_HELPSCOUT_CLIENT_SECRET"],
        "refresh_token": _refresh_token(),
        "user_agent": "peliqan-regression-capture/1.0",
        "start_date": os.getenv("TAP_HELPSCOUT_START_DATE", "2024-01-01T00:00:00Z"),
    }
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(config, tmp)
    tmp.close()
    return tmp.name


def _env():
    env = os.environ.copy()
    env.setdefault("AES_SECRET_KEY", "peliqan-test-key")
    return env


def discover(config_path):
    print("Discovering catalog...")
    result = subprocess.run(
        [TAP_CMD, "--config", config_path, "--discover"],
        capture_output=True, text=True, env=_env()
    )
    if result.returncode != 0:
        print("ERROR: discover failed.")
        print("STDERR:", result.stderr[-2000:])
        sys.exit(1)
    catalog = json.loads(result.stdout)
    print(f"Discovered {len(catalog.get('streams', []))} streams.")
    return catalog


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


def run_sync(config_path, catalog_path):
    print(f"Syncing with Python {sys.version.split()[0]}...")
    result = subprocess.run(
        [TAP_CMD, "--config", config_path, "--catalog", catalog_path],
        capture_output=True, text=True, env=_env()
    )
    if result.returncode != 0:
        print("WARNING: tap exited non-zero. Capturing partial output.")
    if result.stderr:
        print("STDERR (last 3000 chars):", result.stderr[-3000:])
    return result.stdout


def parse_output(output):
    schemas, records, states = {}, {}, []
    for line in output.strip().splitlines():
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


def extract_state_keys(states):
    keys = {}
    for state in states:
        for stream, bookmark in state.get("bookmarks", {}).items():
            keys[stream] = sorted(bookmark.keys()) if isinstance(bookmark, dict) else []
    return keys


def extract_record_fields(records):
    fields = {}
    for stream, stream_records in records.items():
        all_fields = set()
        for record in stream_records:
            all_fields.update(record.keys())
        fields[stream] = sorted(all_fields)
    return fields


def main():
    check_env()

    include = os.getenv("TAP_HELPSCOUT_INCLUDE_STREAMS", "mailboxes,users,teams")
    only_streams = [s.strip() for s in include.split(",") if s.strip()]
    print(f"Target streams: {only_streams}")

    config_path = write_config()
    catalog_path = None

    try:
        # Keep all streams in the catalog so the tap can look up child stream
        # entries — only mark our targets as selected.
        catalog = discover(config_path)
        select_streams(catalog, only_streams)
        catalog_path = write_catalog(catalog)

        output = run_sync(config_path, catalog_path)
        schemas, records, states = parse_output(output)

        BASELINE_DIR.mkdir(parents=True, exist_ok=True)

        (BASELINE_DIR / "catalog.json").write_text(
            json.dumps(catalog, indent=2, sort_keys=True)
        )
        (BASELINE_DIR / "schemas.json").write_text(
            json.dumps(schemas, indent=2, sort_keys=True)
        )
        print(f"Saved schemas for {len(schemas)} streams: {list(schemas.keys())}")

        state_keys = extract_state_keys(states)
        (BASELINE_DIR / "state_keys.json").write_text(
            json.dumps(state_keys, indent=2, sort_keys=True)
        )
        print(f"Saved state keys for {len(state_keys)} streams")

        record_fields = extract_record_fields(records)
        (BASELINE_DIR / "record_fields.json").write_text(
            json.dumps(record_fields, indent=2, sort_keys=True)
        )
        total = sum(len(v) for v in records.values())
        print(f"Saved record fields for {len(record_fields)} streams ({total} records total)")

        (BASELINE_DIR / "records.json").write_text(
            json.dumps(records, indent=2, sort_keys=True, default=str)
        )
        print(f"Saved raw records to {BASELINE_DIR / 'records.json'}")

        python_version = sys.version.split()[0]
        meta = {"python_version": python_version, "streams": list(schemas.keys())}
        (BASELINE_DIR / "meta.json").write_text(json.dumps(meta, indent=2))

        # Persist the rotated refresh_token so the next run can use it
        try:
            with open(config_path) as f:
                final = json.load(f)
            new_tok = final.get("refresh_token")
            if new_tok:
                ROTATED_TOKEN_FILE.write_text(new_tok)
                print(f"Saved rotated refresh_token to {ROTATED_TOKEN_FILE}")
        except Exception as e:
            print(f"WARNING: could not persist rotated token: {e}")

        print(f"\nBaseline captured on Python {python_version}.")
        print(f"Files written to: {BASELINE_DIR}")

    finally:
        os.unlink(config_path)
        if catalog_path:
            os.unlink(catalog_path)


if __name__ == "__main__":
    main()
