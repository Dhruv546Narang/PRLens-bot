import logging

from app.services import github, diff_processor, llm
from app.config import MAX_DIFF_CHARS, MAX_FILE_CHARS, MAX_FILES, BLOCK_ON_ISSUES

logger = logging.getLogger("prlens")


# ──────────────────────────────────────────────────────────
#  Emoji Maps
# ──────────────────────────────────────────────────────────

SEVERITY_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
    "nitpick": "⚪",
}

RATING_EMOJI = {
    "F": "💀",
    "D": "😬",
    "C": "😐",
    "B": "👍",
    "A": "🌟",
}

CATEGORY_EMOJI = {
    "security": "🔒",
    "bug": "🐛",
    "performance": "⚡",
    "architecture": "🏗️",
    "quality": "🧹",
    "error_handling": "⚠️",
    "missing": "📝",
}


# ──────────────────────────────────────────────────────────
#  Review Body Formatter
# ──────────────────────────────────────────────────────────

def format_review_body(
    file_reviews: list[dict],
    total_files: int,
    skipped_count: int,
    failed_files: list[str],
) -> tuple[str, str]:
    """Build the final review body markdown and determine the review event.

    Returns (body_markdown, event) where event is COMMENT or REQUEST_CHANGES.
    """
    # ── Aggregate stats ──
    total_issues = 0
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "nitpick": 0}
    ratings = []

    for entry in file_reviews:
        review = entry.get("review", {})
        issues = review.get("issues", [])
        total_issues += len(issues)
        for issue in issues:
            sev = issue.get("severity", "low")
            if sev in severity_counts:
                severity_counts[sev] += 1
        rating = review.get("rating", "?")
        ratings.append({"file": entry["file"], "rating": rating, "summary": review.get("summary", "")})

    # ── Overall rating = worst file rating ──
    rating_order = ["F", "D", "C", "B", "A"]
    overall_rating = "A"
    for r in ratings:
        rat = r["rating"]
        if rat in rating_order:
            if rating_order.index(rat) < rating_order.index(overall_rating):
                overall_rating = rat

    has_blockers = severity_counts["critical"] > 0 or severity_counts["high"] > 0

    # ── Build markdown ──
    body = "# 🔍 PRLens Code Review\n\n"
    body += f"## Overall Rating: {overall_rating} {RATING_EMOJI.get(overall_rating, '')}\n\n"

    if has_blockers:
        body += "**⛔ This PR has blocking issues. DO NOT merge until they are fixed.**\n\n"
    elif total_issues > 5:
        body += "**This PR needs work. Address the issues below.**\n\n"
    elif total_issues > 0:
        body += "**Minor issues found. See details below.**\n\n"
    else:
        body += "**Clean PR. No significant issues found.**\n\n"

    # Severity table
    body += "### Issue Breakdown\n\n"
    body += "| Severity | Count |\n|---|---|\n"
    for sev in ["critical", "high", "medium", "low", "nitpick"]:
        count = severity_counts[sev]
        if count > 0:
            body += f"| {SEVERITY_EMOJI.get(sev, '')} **{sev.upper()}** | {count} |\n"
    if total_issues == 0:
        body += "| ✅ None | 0 |\n"
    body += f"\n**{total_issues} issues** across **{len(file_reviews)} files** reviewed.\n\n"

    # Per-file ratings
    body += "### File Ratings\n\n"
    body += "| File | Rating | Verdict |\n|---|---|---|\n"
    for r in ratings:
        summary = r["summary"]
        if len(summary) > 150:
            summary = summary[:147] + "..."
        rat = r["rating"]
        body += f"| `{r['file']}` | {rat} {RATING_EMOJI.get(rat, '')} | {summary} |\n"
    body += "\n"

    # Non-inline issues (low/nitpick severity — don't clutter inline comments)
    non_inline_issues = []
    for entry in file_reviews:
        review = entry.get("review", {})
        reviewable = entry.get("reviewable_lines", set())
        for issue in review.get("issues", []):
            line = issue.get("line")
            sev = issue.get("severity", "low")
            # Include issues that couldn't be posted inline
            if line is None or line not in reviewable:
                non_inline_issues.append({"file": entry["file"], **issue})

    if non_inline_issues:
        body += "### Additional Findings\n\n"
        body += "*These issues could not be posted as inline comments:*\n\n"
        for issue in non_inline_issues:
            sev = issue.get("severity", "low")
            cat = issue.get("category", "quality")
            body += (
                f"- {SEVERITY_EMOJI.get(sev, '⚪')} **{issue.get('title', 'Issue')}** "
                f"(`{issue['file']}:{issue.get('line', '?')}`) — "
                f"{issue.get('detail', '')}\n"
            )
        body += "\n"

    # Failed files
    if failed_files:
        body += "### ⚠️ Review Failures\n\n"
        body += "Could not review these files (will retry on next push):\n\n"
        for f in failed_files:
            body += f"- `{f}`\n"
        body += "\n"

    if skipped_count > 0:
        body += f"*{skipped_count} files skipped (lock files, binaries, generated code)*\n\n"

    body += "---\n"
    body += f"*Powered by [PRLens](https://github.com) · {llm.REVIEW_MODEL} via OpenRouter*"

    event = "COMMENT"
    if BLOCK_ON_ISSUES and has_blockers:
        event = "REQUEST_CHANGES"

    return body, event


# ──────────────────────────────────────────────────────────
#  Inline Comment Builder
# ──────────────────────────────────────────────────────────

def build_inline_comments(file_reviews: list[dict]) -> list[dict]:
    """Build GitHub inline review comments from LLM output.

    Only includes issues where the line is actually in the diff
    (GitHub rejects comments on lines not in the diff).
    """
    comments = []

    for entry in file_reviews:
        file_path = entry["file"]
        reviewable = entry.get("reviewable_lines", set())
        review = entry.get("review", {})

        for issue in review.get("issues", []):
            line = issue.get("line")

            # Only post inline if the line is actually in the diff
            if line is None or line not in reviewable:
                continue

            severity = issue.get("severity", "low")
            category = issue.get("category", "quality")
            title = issue.get("title", "Issue found")
            detail = issue.get("detail", "")
            suggestion = issue.get("suggestion", "")

            # Build the comment body
            comment_body = (
                f"{SEVERITY_EMOJI.get(severity, '⚪')} **{severity.upper()}** · "
                f"{CATEGORY_EMOJI.get(category, '')} {category.capitalize()}\n\n"
                f"### {title}\n\n"
                f"{detail}\n"
            )
            if suggestion:
                comment_body += f"\n💡 **Fix:** {suggestion}\n"

            comments.append({
                "path": file_path,
                "line": line,
                "side": "RIGHT",
                "body": comment_body,
            })

    return comments


# ──────────────────────────────────────────────────────────
#  Main Review Pipeline
# ──────────────────────────────────────────────────────────

def run_review(data: dict) -> None:
    """Orchestrate the full review: fetch diff → LLM review → post to GitHub.

    Called as a FastAPI background task.
    """
    repo = data["repository"]["full_name"]
    pr_num = data["pull_request"]["number"]
    inst_id = data["installation"]["id"]
    pr_title = data["pull_request"].get("title", "")
    pr_author = data["pull_request"].get("user", {}).get("login", "unknown")

    logger.info(f"{'='*60}")
    logger.info(f"PR #{pr_num}: '{pr_title}' by @{pr_author} in {repo}")
    logger.info(f"{'='*60}")

    # ── 1. Authenticate ──
    try:
        token = github.get_installation_token(inst_id)
        logger.info("Authentication successful")
    except Exception as e:
        logger.error(f"Auth failed for installation {inst_id}: {e}")
        return

    # ── 2. Fetch diff ──
    try:
        raw_diff = github.fetch_pr_diff(repo, pr_num, token)
        logger.info(f"Fetched diff: {len(raw_diff):,} characters")
    except Exception as e:
        logger.error(f"Failed to fetch diff: {e}")
        return

    # ── 3. Guard: refuse massive PRs ──
    if len(raw_diff) > MAX_DIFF_CHARS:
        logger.warning(f"Diff too large: {len(raw_diff):,} chars (limit: {MAX_DIFF_CHARS:,})")
        github.post_review(
            repo, pr_num, token,
            "# 🔍 PRLens Code Review\n\n"
            f"**⚠️ This PR is too large to review** ({len(raw_diff):,} characters).\n\n"
            "Large PRs are harder to review, easier to hide bugs in, and slower to merge. "
            "Break this into smaller, focused PRs.\n\n"
            "---\n*Powered by PRLens*"
        )
        return

    # ── 4. Parse & filter diffs ──
    file_diffs = diff_processor.parse_diff(raw_diff)
    filtered_diffs = diff_processor.filter_diffs(file_diffs, max_files=MAX_FILES)
    skipped_count = len(file_diffs) - len(filtered_diffs)

    if not filtered_diffs:
        logger.info("No reviewable files found")
        github.post_review(
            repo, pr_num, token,
            "# 🔍 PRLens Code Review\n\n"
            "No reviewable source files in this PR "
            "(all files are lock files, binaries, or generated code).\n\n"
            "---\n*Powered by PRLens*"
        )
        return

    logger.info(
        f"Files: {len(filtered_diffs)} to review, {skipped_count} skipped | "
        f"Reviewing: {', '.join(fd.path for fd in filtered_diffs[:10])}"
    )

    # ── 5. Review each file ──
    file_reviews: list[dict] = []
    failed_files: list[str] = []

    for i, fd in enumerate(filtered_diffs, 1):
        logger.info(f"[{i}/{len(filtered_diffs)}] Reviewing: {fd.path}")

        # Skip individual files that are too large
        if len(fd.raw_diff) > MAX_FILE_CHARS:
            logger.warning(f"  Skipped (too large: {len(fd.raw_diff):,} chars)")
            failed_files.append(f"{fd.path} (too large: {len(fd.raw_diff):,} chars)")
            continue

        reviewable_lines = fd.reviewable_lines
        result = llm.review_file(fd.path, fd.raw_diff, reviewable_lines)

        if result:
            file_reviews.append({
                "file": fd.path,
                "review": result,
                "reviewable_lines": reviewable_lines,
            })
        else:
            failed_files.append(fd.path)

    # ── 6. Handle total failure ──
    if not file_reviews:
        logger.error("All file reviews failed")
        github.post_review(
            repo, pr_num, token,
            "# 🔍 PRLens Code Review\n\n"
            "**⚠️ Review failed** — could not analyze any files in this PR. "
            "This is likely a temporary issue with the AI service. "
            "Push another commit to trigger a re-review.\n\n"
            "---\n*Powered by PRLens*"
        )
        return

    # ── 7. Format & post ──
    body, event = format_review_body(
        file_reviews, len(filtered_diffs), skipped_count, failed_files
    )
    inline_comments = build_inline_comments(file_reviews)

    total_issues = sum(
        len(entry.get("review", {}).get("issues", []))
        for entry in file_reviews
    )

    logger.info(
        f"Review ready: {total_issues} issues, "
        f"{len(inline_comments)} inline comments, "
        f"event={event}"
    )

    try:
        github.post_review(repo, pr_num, token, body, inline_comments, event)
        logger.info(f"✅ Review posted for PR #{pr_num} in {repo}")
    except Exception as e:
        logger.error(f"Failed to post review: {e}")
