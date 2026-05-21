#!/usr/bin/env python3
"""
Upload a Codabench competition bundle (.zip) via the public REST API.

API reference: https://www.codabench.org/api/docs/
Implementation follows the datasets flow in codalab/codabench (DataViewSet.create +
upload_completed → unpack_competition).

Usage:
  export CODABENCH_TOKEN="..."   # from POST /api/api-token-auth/ with username/password
  python codabench/upload_bundle.py competition_bundle.zip

  # or login first:
  python codabench/upload_bundle.py --username USER --password PASS bundle.zip

Environment:
  CODABENCH_BASE_URL  default https://www.codabench.org
  CODABENCH_TOKEN    DRF token (Authorization: Token …)

This script auto-loads environment variables from `codabench/.env` (next to this
file) before parsing arguments, and exits with an error if that file is missing
or invalid.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import urllib.error
import urllib.request
from dotenv import load_dotenv


def _json_request(
    method: str,
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    data: Optional[bytes | dict[str, Any]] = None,
    raw_body: Optional[bytes] = None,
):
    hdrs = dict(headers or {})
    body: Optional[bytes] = raw_body
    if data is not None and raw_body is None:
        body = json.dumps(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=body, method=method, headers=hdrs)

    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype:
                try:
                    return resp.status, json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    return resp.status, {"_raw_text": raw.decode("utf-8", errors="replace")}
            return resp.status, raw
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(detail)
        except json.JSONDecodeError:
            err_json = {"detail": detail, "status_code": e.code}
        msg = f"HTTP {e.code} {method} {url}\n{json.dumps(err_json, indent=2)}"
        raise SystemExit(msg) from e


def obtain_token(base: str, username: str, password: str):
    url = base.rstrip("/") + "/api/api-token-auth/"
    _, payload = _json_request("POST", url, data={"username": username, "password": password})
    assert isinstance(payload, dict), payload
    token = payload.get("token")
    if not token:
        raise ValueError(f"Unexpected token response: {payload}")
    return str(token)


def create_dataset_placeholder(
    base: str,
    token: str,
    *,
    name: str,
    zip_filename: str,
    file_size: float,
    description: str = "",
    is_public: bool = False,
):
    url = base.rstrip("/") + "/api/datasets/"
    body = {
        "name": name,
        "type": "competition_bundle",
        "description": description,
        "is_public": is_public,
        "file_size": file_size,
        "request_sassy_file_name": zip_filename,
    }
    _, payload = _json_request(
        "POST",
        url,
        headers={"Authorization": f"Token {token}"},
        data=body,
    )
    assert isinstance(payload, dict), payload
    return payload


def put_zip_to_signed_url(sassy_url: str, zip_path: Path):
    data = zip_path.read_bytes()
    headers = {
        "Content-Type": "application/zip",
        # Azure-friendly; ignored by S3/GCS
        "x-ms-blob-type": "BlockBlob",
    }
    req = urllib.request.Request(sassy_url, data=data, method="PUT", headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status not in (200, 201, 204):
                raise ValueError(f"Upload failed with HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Upload to storage failed HTTP {e.code}: {body}") from e


def finalize_dataset_upload(base: str, token: str, key: str):
    url = base.rstrip("/") + f"/api/datasets/completed/{key}/"
    _, payload = _json_request(
        "PUT",
        url,
        headers={"Authorization": f"Token {token}"},
        data={},
    )
    assert isinstance(payload, dict), payload
    return payload


def poll_creation_status(
    base: str,
    token: str,
    status_id: int | str,
    *,
    poll_interval: float,
    timeout: float,
):
    """Poll GET /api/competitions/<status_id>/creation_status/ until Finished or Failed."""
    deadline = time.monotonic() + timeout
    url = base.rstrip("/") + f"/api/competitions/{status_id}/creation_status/"

    while time.monotonic() < deadline:
        _, payload = _json_request(
            "GET",
            url,
            headers={"Authorization": f"Token {token}"},
        )
        assert isinstance(payload, dict), payload
        status = payload.get("status")
        # CompetitionCreationTaskStatus: "Starting", "Finished", "Failed"
        if status in ("Finished", "Failed"):
            return payload
        time.sleep(poll_interval)

    raise ValueError(f"Timed out waiting for unpack after {timeout}s (last GET {url})")


def parse_args(argv: list[str]):
    p = argparse.ArgumentParser(description="Upload a Codabench competition bundle zip via API.")
    p.add_argument("bundle", type=Path, help="Path to competition bundle .zip")
    p.add_argument("--base-url", default=None, help="API base URL")
    p.add_argument("--token", default=None, help="DRF API token or set CODABENCH_TOKEN in .env")
    p.add_argument("--username", help="Codabench username (fetches token if password set)")
    p.add_argument("--password", help="Codabench password")
    return p.parse_args(argv)


def main(argv):
    print("[*] Loading env")
    load_dotenv()

    print("[*] Parsing args")
    args = parse_args(argv)
    bundle: Path = args.bundle

    if not bundle.is_file():
        raise FileNotFoundError(f"Bundle not found: {bundle}")

    if bundle.suffix.lower() != ".zip":
        raise ValueError("Bundle must end with .zip")

    zip_size = float(bundle.stat().st_size)

    base_url = args.base_url or os.environ.get("CODABENCH_BASE_URL", "https://www.codabench.org")

    print("[*] Authentication Token")
    token = args.token or os.environ.get("CODABENCH_TOKEN", None)
    if args.username and args.password:
        token = obtain_token(base_url, args.username, args.password)
    elif not token:
        raise ValueError("Provide --token or --username and --password. Alternatively, set CODABENCH_TOKEN in .env.")

    dataset_name = bundle.stem

    print("[*] Creating dataset")
    created = create_dataset_placeholder(
        base_url,
        token,
        name=dataset_name,
        zip_filename=bundle.name,
        file_size=zip_size,
    )

    dataset_key = str(created["key"])
    sassy_url = str(created["sassy_url"])
    print(f"Dataset key={dataset_key}\nPutting ZIP to signed URL…\n")
    put_zip_to_signed_url(sassy_url, bundle)

    print("[*] Finalizing dataset upload")
    final = finalize_dataset_upload(base_url, token, dataset_key)
    print(json.dumps(final, indent=2))

    status_id = final.get("status_id")
    if status_id is None:
        print("No status_id in finalize response; skip wait")
        return

    print(f"[*] Waiting for unpack status_id={status_id}")
    outcome = poll_creation_status(
        base_url,
        token,
        status_id,
        poll_interval=3.0,
        timeout=60,
    )
    print(json.dumps({"creation_status": outcome}, indent=2))

    status = outcome.get("status")
    if status != "Finished":
        print(json.dumps(outcome, indent=2) + "\n")
        return

    comp = outcome.get("resulting_competition")
    if comp is not None:
        cid = comp if isinstance(comp, int) else comp.get("pk") or comp.get("id")
        if cid is not None:
            print(f"[+] Competition created with id: {cid}")
            print(f"Competition URL: {f'{base_url}/competitions/{cid}/'}")


if __name__ == "__main__":
    main(sys.argv[1:])
