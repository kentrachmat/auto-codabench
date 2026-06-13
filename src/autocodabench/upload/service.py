"""Publish a built bundle zip to Codabench via the public REST API.

Thin orchestration over the canonical 4-step flow in
:mod:`autocodabench.upload.codabench_api` (token → dataset placeholder →
signed PUT → finalize + poll).

Auth credentials come from arguments first, environment second:
  - CODABENCH_BASE_URL  (default https://www.codabench.org)
  - CODABENCH_USERNAME + CODABENCH_PASSWORD, OR
  - CODABENCH_TOKEN

If both forms are available, username+password takes precedence: a fresh
token is obtained on every call, so scheduled token rotation (Codabench
tokens expire after 90 days) cannot invalidate a stored configuration
mid-run.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from . import codabench_api as helpers

log = logging.getLogger("autocodabench.upload")


def upload_zip(
    zip_path: Path,
    *,
    username: str | None = None,
    password: str | None = None,
    token: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Publish an already-built zip to Codabench. Credentials-explicit.

    Used by the MCP `autocodabench_upload_bundle` tool (which pulls
    creds from env) and by the web-UI `/ac/upload-codabench` route
    (which passes user-supplied creds from the workspace form). Pulling
    from env is therefore optional — callers can fully override.

    Returns a dict with `competition_id` + `competition_url` on success,
    or `{"error": "..."}` on any failure. Never raises.
    """
    if not zip_path.is_file():
        return {"error": f"zip not found at {zip_path}. Run zip_bundle first."}

    base_url = base_url or os.environ.get(
        "CODABENCH_BASE_URL", "https://www.codabench.org")
    username = username or os.environ.get("CODABENCH_USERNAME")
    password = password or os.environ.get("CODABENCH_PASSWORD")
    token    = token    or os.environ.get("CODABENCH_TOKEN")

    if not (username and password) and not token:
        return {
            "error": (
                "Missing Codabench credentials. Provide username + password "
                "(via the workspace form) or set CODABENCH_TOKEN."
            )
        }

    log.info("upload_zip path=%s size=%d", zip_path, zip_path.stat().st_size)

    try:
        # Step 1: fetch a fresh token if we have user/pass.
        if username and password:
            token = helpers.obtain_token(base_url, username, password)
        assert token  # validated above

        # Step 2: create the dataset placeholder (returns a signed PUT URL).
        created = helpers.create_dataset_placeholder(
            base_url, token,
            name=zip_path.stem,
            zip_filename=zip_path.name,
            file_size=float(zip_path.stat().st_size),
        )
        dataset_key = str(created["key"])
        sassy_url = str(created["sassy_url"])

        # Step 3: PUT the zip bytes to the signed URL.
        helpers.put_zip_to_signed_url(sassy_url, zip_path)

        # Step 4: tell Codabench the upload is done; receive a status_id to poll.
        final = helpers.finalize_dataset_upload(base_url, token, dataset_key)
        status_id = final.get("status_id")
        if status_id is None:
            return {
                "error": "Codabench returned no status_id; cannot poll. Raw response: " + str(final)
            }

        # Step 5: poll until Codabench finishes unpacking.
        outcome = helpers.poll_creation_status(
            base_url, token, status_id,
            poll_interval=3.0, timeout=120.0,
        )
    except SystemExit as e:
        # codabench_api raises SystemExit on storage errors; convert.
        return {"error": f"Upload helper raised SystemExit: {e}"}
    except Exception as e:
        return {"error": f"Upload failed: {type(e).__name__}: {e}"}

    if outcome.get("status") != "Finished":
        return {
            "error": (
                f"Codabench unpack did not finish (status={outcome.get('status')}). "
                f"Full payload: {outcome}"
            ),
            "raw": outcome,
        }

    # Extract competition id and synthesize the public URL.
    comp = outcome.get("resulting_competition")
    if isinstance(comp, int):
        cid: Any = comp
    elif isinstance(comp, dict):
        cid = comp.get("pk") or comp.get("id")
    else:
        cid = None

    if cid is None:
        return {
            "error": "Unpack finished but Codabench did not return a competition id.",
            "raw": outcome,
        }

    return {
        "competition_id": cid,
        "competition_url": f"{base_url.rstrip('/')}/competitions/{cid}/",
        "raw": outcome,
    }
