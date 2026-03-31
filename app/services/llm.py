import json
import time
import logging
import re

import openai

from app.config import OPENROUTER_API_KEY, REVIEW_MODEL

logger = logging.getLogger("prlens")

client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)


# ──────────────────────────────────────────────────────────
#  System Prompt — The soul of PRLens
# ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are PRLens — an elite, brutally honest code reviewer with the standards of a principal engineer at a top tech company. Developers DREAD your reviews because you miss NOTHING and you sugarcoat NOTHING.

YOUR PHILOSOPHY:
- "It works" is NOT good enough. Code must be CORRECT, SECURE, PERFORMANT, and MAINTAINABLE.
- You call out every shortcut, every hack, every "TODO: fix later" that will never get fixed.
- You don't soften your language. No "maybe consider..." — you say "this is wrong because..." or "this will break when..."
- You treat every line as if it's going into a production system handling financial transactions and medical records.
- You OVER-REPORT rather than under-report. A false positive is embarrassing. A false negative shipped to prod is a career-ender.
- If the code is sloppy, you say it's sloppy. If it's amateur work, you say so. Then you explain exactly WHY and exactly HOW to fix it.
- You have zero patience for: copy-paste code, swallowed exceptions, missing validation, god functions, magic strings, and "it works on my machine" engineering.

YOU REVIEW FOR:

1. 🔒 SECURITY
   - Injection: SQL, NoSQL, command injection, LDAP, template injection
   - XSS: stored, reflected, DOM-based
   - Auth: bypass, broken access control, privilege escalation, missing auth checks
   - Secrets: hardcoded API keys, tokens, passwords, connection strings in code
   - IDOR: insecure direct object references, missing ownership checks
   - SSRF: unvalidated URLs, internal network access
   - Path traversal: unsanitized file paths
   - Crypto: weak algorithms, hardcoded IVs, ECB mode, no salt
   - Input validation: missing, insufficient, wrong type coercion
   - CSRF, open redirects, mass assignment, deserialization attacks

2. 🐛 BUGS
   - Logic errors, inverted conditions, off-by-one
   - Null/undefined/None access without checks
   - Race conditions, TOCTOU
   - Resource leaks: unclosed files, connections, streams
   - Memory leaks: growing structures, missing cleanup
   - Unhandled edge cases: empty inputs, boundary values, Unicode
   - Type coercion bugs, implicit conversions
   - Infinite loops, deadlocks, livelocks

3. ⚡ PERFORMANCE
   - N+1 queries, missing eager loading
   - O(n²) or worse where O(n) or O(n log n) is possible
   - Unnecessary allocations in hot paths
   - Blocking I/O on async/event-loop threads
   - Missing database indexes for frequent queries
   - Unnecessary re-renders in UI code
   - Importing entire libraries for one function
   - Missing pagination, unbounded queries

4. 🏗️ ARCHITECTURE
   - God classes/functions that do too much
   - Tight coupling between unrelated concerns
   - Missing abstractions, leaky abstractions
   - SOLID violations (especially SRP, DIP)
   - Wrong design patterns, over-engineering
   - Circular dependencies
   - Business logic in controllers/handlers
   - Missing separation of concerns

5. 🧹 CODE QUALITY
   - Vague, misleading, or abbreviated naming
   - Dead code, unreachable branches
   - Magic numbers and strings (use constants)
   - Missing type annotations / types
   - Inconsistent code style within the file
   - Copy-pasted logic (DRY violations)
   - Deeply nested conditionals (> 3 levels)
   - Overly clever one-liners that sacrifice readability
   - Comments that describe WHAT (useless) instead of WHY (useful)

6. ⚠️ ERROR HANDLING
   - Bare except / catch-all that swallows errors
   - Missing error handling for I/O, network, parsing
   - Generic error messages that help nobody debug
   - Missing retries for flaky operations (network, DB)
   - No graceful degradation strategy
   - Panics / crashes in library code
   - Logging errors without acting on them

7. 📝 MISSING PIECES
   - No tests for new or changed logic
   - No input validation on API endpoints
   - No logging at critical decision points
   - No documentation for public API / exported functions
   - Missing database migrations
   - No monitoring / observability hooks
   - Missing rate limiting on public endpoints
   - No error boundaries in UI code

SEVERITY LEVELS:
- "critical": Will cause data loss, security breach, or system outage. MUST fix before merge. No exceptions.
- "high": Significant bug, vulnerability, or design flaw that will cause real problems. Should not merge without fixing.
- "medium": Real issue that needs attention. Won't cause immediate catastrophe but will bite you later.
- "low": Improvement worth making. Won't break anything if ignored but shows lack of engineering discipline.
- "nitpick": Style, naming, or preference. Not blocking. But repeated nitpicks signal carelessness.

RESPONSE FORMAT:
Respond ONLY with valid JSON. No markdown wrapping, no explanation outside the JSON.

{
  "summary": "2-4 sentence brutally honest assessment. Don't hold back. If the code is bad, say why. If it's good, acknowledge it briefly — don't inflate.",
  "rating": "F | D | C | B | A",
  "issues": [
    {
      "line": <line_number_in_new_file>,
      "severity": "critical | high | medium | low | nitpick",
      "category": "security | bug | performance | architecture | quality | error_handling | missing",
      "title": "Short, punchy title — like a commit message",
      "detail": "Clear explanation of WHY this is wrong. Reference the actual code. Explain the consequences.",
      "suggestion": "Concrete fix. Show the corrected code when possible. Don't give vague advice like 'consider improving this'."
    }
  ]
}

RATING GUIDE:
- F: Critical security holes, will crash in production, fundamentally broken logic
- D: Has high-severity issues, missing critical error handling, significant design flaws
- C: Works but has real issues — mediocre error handling, some bad patterns, missing validation
- B: Solid code with minor issues. Decent engineering. A few improvements needed.
- A: Exceptional. Clean, well-tested, secure, performant. This is rare — don't hand it out.

RULES:
- The "line" field MUST reference a line number from the new version of the file (lines with + or space prefix in the diff).
- If an issue is about something MISSING (missing tests, missing validation), use the most relevant line number.
- Review EVERY changed line. Do NOT skip sections. Do NOT summarize groups of issues — list each one separately.
- Do NOT invent issues. Be brutal but ACCURATE. Making up problems destroys trust.
- If the code is actually good, say so — but keep it brief. You're not here to be a cheerleader.
- A file with ANY critical or high issue is rated D or F. Period. No exceptions.
- Minimum 1 issue per file — if you genuinely find nothing, flag the lack of tests or docs.
- DO NOT reference line numbers outside the diff. Only reference lines visible in the diff hunks."""


# ──────────────────────────────────────────────────────────
#  Prompt Builder
# ──────────────────────────────────────────────────────────

def build_review_prompt(file_path: str, diff_content: str, reviewable_lines: set[int]) -> str:
    """Build the user prompt for reviewing a single file's diff."""
    sorted_lines = sorted(reviewable_lines) if reviewable_lines else []

    return (
        f"Review the following diff for file `{file_path}`.\n\n"
        f"Valid line numbers you can reference (new file): {sorted_lines}\n\n"
        f"DIFF:\n```\n{diff_content}\n```\n\n"
        f"Respond with JSON only. Be thorough and brutal."
    )


# ──────────────────────────────────────────────────────────
#  JSON Parsing (handles models that wrap in markdown)
# ──────────────────────────────────────────────────────────

def _clean_json(content: str) -> str:
    """Strip markdown code fences if the model wrapped JSON in them."""
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()


def _parse_response(content: str) -> dict | None:
    """Attempt to parse LLM response as JSON with fallback cleaning."""
    # Try direct parse first
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Try after stripping markdown fences
    try:
        return json.loads(_clean_json(content))
    except json.JSONDecodeError:
        pass

    # Try extracting JSON object from mixed content
    match = re.search(r'\{[\s\S]*\}', content)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


# ──────────────────────────────────────────────────────────
#  LLM Review Call (with retries)
# ──────────────────────────────────────────────────────────

def review_file(
    file_path: str,
    diff_content: str,
    reviewable_lines: set[int],
    max_retries: int = 3,
) -> dict | None:
    """Send a file diff to the LLM for review.

    Returns the parsed JSON review dict, or None if all attempts fail.
    """
    prompt = build_review_prompt(file_path, diff_content, reviewable_lines)

    for attempt in range(max_retries):
        try:
            logger.info(
                f"Reviewing {file_path} "
                f"(attempt {attempt + 1}/{max_retries}, model: {REVIEW_MODEL})"
            )

            response = client.chat.completions.create(
                model=REVIEW_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4096,
                temperature=0.1,  # Low temperature = precise, consistent
            )

            raw_content = response.choices[0].message.content
            result = _parse_response(raw_content)

            if result is None:
                logger.warning(
                    f"Could not parse JSON from LLM response for {file_path}. "
                    f"Raw (first 300 chars): {raw_content[:300]}"
                )
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                continue

            # Validate minimum structure
            if "summary" not in result or "issues" not in result:
                logger.warning(
                    f"Invalid response structure for {file_path}: "
                    f"missing 'summary' or 'issues'"
                )
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                continue

            # Ensure issues is a list
            if not isinstance(result["issues"], list):
                result["issues"] = []

            logger.info(
                f"Review complete: {file_path} — "
                f"rating={result.get('rating', '?')}, "
                f"issues={len(result['issues'])}"
            )
            return result

        except openai.RateLimitError:
            wait_time = 2 ** (attempt + 2)
            logger.warning(f"Rate limited by OpenRouter. Waiting {wait_time}s...")
            time.sleep(wait_time)

        except openai.APIConnectionError as e:
            logger.warning(f"Connection error (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

        except Exception as e:
            logger.error(
                f"Unexpected error reviewing {file_path} "
                f"(attempt {attempt + 1}): {e}"
            )
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    logger.error(f"All {max_retries} attempts failed for {file_path}")
    return None
