import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────
#  GitHub App
# ──────────────────────────────────────────────────────────
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
PEM_FILE_PATH = os.getenv("PEM_FILE_PATH")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")

# ──────────────────────────────────────────────────────────
#  OpenRouter
# ──────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Primary model for code review — this is where quality matters.
# Claude Sonnet 4 is best-in-class for code analysis and brutal honesty.
# Cost: ~$3/M input, $15/M output — a typical 5-file PR costs ~$0.02-0.05
REVIEW_MODEL = os.getenv("REVIEW_MODEL", "anthropic/claude-sonnet-4")

# ──────────────────────────────────────────────────────────
#  Review Behaviour
# ──────────────────────────────────────────────────────────
# Max total diff size (chars) before we refuse to review
MAX_DIFF_CHARS = int(os.getenv("MAX_DIFF_CHARS", "120000"))

# Max single-file diff size (chars)
MAX_FILE_CHARS = int(os.getenv("MAX_FILE_CHARS", "25000"))

# Max files to review per PR
MAX_FILES = int(os.getenv("MAX_FILES", "30"))

# Whether to use REQUEST_CHANGES (blocks merge) when critical/high issues found
BLOCK_ON_ISSUES = os.getenv("BLOCK_ON_ISSUES", "true").lower() == "true"

# ──────────────────────────────────────────────────────────
#  Validation — fail fast if config is wrong
# ──────────────────────────────────────────────────────────
GITHUB_PRIVATE_KEY = os.getenv("GITHUB_PRIVATE_KEY")

_required = {
    "GITHUB_APP_ID": GITHUB_APP_ID,
    "GITHUB_WEBHOOK_SECRET": GITHUB_WEBHOOK_SECRET,
    "OPENROUTER_API_KEY": OPENROUTER_API_KEY,
}
# Require either the PEM file or the env var
if not GITHUB_PRIVATE_KEY and not PEM_FILE_PATH:
    _required["PEM_FILE_PATH (or GITHUB_PRIVATE_KEY)"] = None

_missing = [k for k, v in _required.items() if not v]
if _missing:
    raise RuntimeError(f"Missing required env vars: {', '.join(_missing)}")

if not GITHUB_PRIVATE_KEY and PEM_FILE_PATH and not Path(PEM_FILE_PATH).exists():
    raise RuntimeError(f"PEM file not found at: {PEM_FILE_PATH}")
