import json
import logging

from fastapi import FastAPI, Request, BackgroundTasks

from app.services import github, reviewer

# ──────────────────────────────────────────────────────────
#  Logging
# ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(name)-8s │ %(levelname)-7s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("prlens")


# ──────────────────────────────────────────────────────────
#  App
# ──────────────────────────────────────────────────────────

app = FastAPI(
    title="PRLens",
    description="Brutally honest AI code reviewer — built as a GitHub App",
    version="1.0.0",
)


# ──────────────────────────────────────────────────────────
#  Webhook Endpoint
# ──────────────────────────────────────────────────────────

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive GitHub webhook events and queue PR reviews."""
    body = await github.verify_webhook_signature(request)
    data = json.loads(body)

    event_type = request.headers.get("X-GitHub-Event", "")
    action = data.get("action", "")

    logger.info(f"Webhook: {event_type}/{action}")

    # Only review on PR opened or new commits pushed
    if event_type == "pull_request" and action in ("opened", "synchronize"):
        pr = data.get("pull_request", {})
        logger.info(
            f"Queuing review: PR #{pr.get('number')} "
            f"'{pr.get('title', '')}' by @{pr.get('user', {}).get('login', '?')}"
        )
        background_tasks.add_task(reviewer.run_review, data)
        return {"status": "review_queued"}

    return {"status": "ignored", "event": event_type, "action": action}


# ──────────────────────────────────────────────────────────
#  Health Check
# ──────────────────────────────────────────────────────────

@app.get("/")
def health():
    """Health check endpoint."""
    from app.config import REVIEW_MODEL
    return {
        "app": "PRLens",
        "status": "running ✅",
        "version": "1.0.0",
        "model": REVIEW_MODEL,
        "description": "Brutally honest AI code reviewer",
    }
