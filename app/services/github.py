import time
import hmac
import hashlib
import logging

import jwt
import requests
from fastapi import Request, HTTPException

from app.config import GITHUB_APP_ID, PEM_FILE_PATH, GITHUB_WEBHOOK_SECRET

logger = logging.getLogger("prlens")


# ──────────────────────────────────────────────────────────
#  Auth — JWT + Installation Tokens
# ──────────────────────────────────────────────────────────

def get_installation_token(installation_id: int) -> str:
    """Generate a short-lived installation access token via JWT."""
    
    # Cloud environments usually provide the raw key in an env var
    private_key = os.getenv("GITHUB_PRIVATE_KEY")
    
    # Fallback to local file if the env var isn't set
    if not private_key:
        with open(PEM_FILE_PATH, "r") as f:
            private_key = f.read()

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": str(GITHUB_APP_ID),
    }
    jwt_token = jwt.encode(payload, private_key, algorithm="RS256")
    if isinstance(jwt_token, bytes):
        jwt_token = jwt_token.decode("utf-8")

    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    resp.raise_for_status()
    return resp.json()["token"]


# ──────────────────────────────────────────────────────────
#  Webhook Signature Verification
# ──────────────────────────────────────────────────────────

async def verify_webhook_signature(request: Request) -> bytes:
    """Verify the X-Hub-Signature-256 header matches the payload."""
    signature = request.headers.get("X-Hub-Signature-256", "")
    body = await request.body()

    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    return body


# ──────────────────────────────────────────────────────────
#  GitHub API Helpers
# ──────────────────────────────────────────────────────────

def _headers(token: str, accept: str = "application/vnd.github+json") -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": accept,
    }


def fetch_pr_diff(repo: str, pr_num: int, token: str) -> str:
    """Fetch the raw unified diff for a pull request."""
    resp = requests.get(
        f"https://api.github.com/repos/{repo}/pulls/{pr_num}",
        headers=_headers(token, accept="application/vnd.github.v3.diff"),
    )
    resp.raise_for_status()
    return resp.text


def fetch_commit_diff(repo: str, commit_sha: str, token: str) -> str:
    """Fetch the raw unified diff for a single commit."""
    resp = requests.get(
        f"https://api.github.com/repos/{repo}/commits/{commit_sha}",
        headers=_headers(token, accept="application/vnd.github.v3.diff"),
    )
    resp.raise_for_status()
    return resp.text


def post_commit_comment(repo: str, commit_sha: str, token: str, body: str) -> None:
    """Post a generic comment on a specific commit."""
    url = f"https://api.github.com/repos/{repo}/commits/{commit_sha}/comments"
    resp = requests.post(url, headers=_headers(token), json={"body": body})
    resp.raise_for_status()
    logger.info(f"Commit comment posted: {resp.status_code}")


def post_review(
    repo: str,
    pr_num: int,
    token: str,
    body: str,
    comments: list[dict] | None = None,
    event: str = "COMMENT",
) -> None:
    """Post a PR review with optional inline comments.

    If inline comments are rejected by GitHub (line not in diff), falls back
    to appending them to the review body.
    """
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_num}/reviews"
    payload = {"body": body, "event": event}

    if comments:
        payload["comments"] = comments

    resp = requests.post(url, headers=_headers(token), json=payload)

    # GitHub returns 422 if any inline comment targets a line not in the diff.
    # Fall back: strip those comments and embed them in the body instead.
    if resp.status_code == 422 and comments:
        logger.warning(
            f"Inline comments rejected by GitHub (422). "
            f"Falling back to body-only. Response: {resp.text[:300]}"
        )
        fallback_section = "\n\n---\n\n### 📌 Additional Findings (could not post inline)\n\n"
        for c in comments:
            path = c.get("path", "?")
            line = c.get("line", "?")
            fallback_section += f"**`{path}:{line}`**\n\n{c['body']}\n\n---\n\n"

        payload = {"body": body + fallback_section, "event": "COMMENT"}
        resp = requests.post(url, headers=_headers(token), json=payload)

    resp.raise_for_status()
    logger.info(f"Review posted: {resp.status_code}")
