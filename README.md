# PRLens 🔍

> **PRLens** is a brutally honest, AI-powered code reviewer built as a GitHub App. It doesn't sugarcoat problems—it tears your code apart and points out security flaws, bugs, and sloppy engineering directly on your Pull Requests where it hurts.

Built with **FastAPI**, **GitHub Apps API**, and **Claude Sonnet 4** via OpenRouter.

---

## What It Does
When someone opens or updates a Pull Request, PRLens:
1. Skips lock files, binaries, and generated code to save tokens.
2. Slices the code changes (the "diff") line by line.
3. Analyzes it aggressively for **Security**, **Bugs**, **Performance**, **Architecture**, **Quality**, and **Error Handling**.
4. Posts brutally honest, inline comments directly on the exact lines of code on GitHub.
5. If the issues are severe, it blocks the PR with a `REQUEST_CHANGES` review.

## Tech Stack
- **Python 3.10+** (FastAPI, Uvicorn, PyJWT)
- **GitHub App Webhooks**
- **LLM**: Anthropic Claude 3.5 Sonnet (via OpenRouter)

---

## Local Setup

### 1. Requirements
- Python 3.10+
- An [OpenRouter API key](https://openrouter.ai/) for the LLM
- A registered [GitHub App](https://docs.github.com/en/apps/creating-github-apps) with the following permissions:
  - **Pull requests**: Read & write
  - **Contents**: Read-only
  - **Events to subscribe to**: Pull request

### 2. Installation
Clone the repo and install the required dependencies:
```bash
git clone https://github.com/your-username/PRLens.git
cd PRLens
python -m venv venv

# Windows
.\venv\Scripts\activate
# Mac/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Configuration
Copy the default environment variables and fill them in:
```bash
cp .env.example .env
```
Inside `.env`, you must supply:
* `GITHUB_APP_ID`: From your GitHub App's "General" settings.
* `GITHUB_WEBHOOK_SECRET`: A secret string you configure in your GitHub app settings to verify webhooks.
* `PEM_FILE_PATH`: The absolute path to the private key `.pem` file you downloaded from GitHub.
* `OPENROUTER_API_KEY`: Your OpenRouter API key.

*(Optional)* You can also tweak review behaviors in `.env` (like maximum files, maximum characters, and which LLM to use).

### 4. Running the Server
Start the local FastAPI server:
```bash
uvicorn app.main:app --port 8000 --reload
```
You can access the server at `http://127.0.0.1:8000`.

### 5. Exposing Localhost (for GitHub Webhooks)
GitHub needs a public URL to send webhooks to. Run **ngrok** in a new terminal window:
```bash
ngrok http 8000
```
Take the public URL ngrok gives you (e.g., `https://xxxx.ngrok.app`) and append `/webhook` to it. Put **THAT** URL in your GitHub App's Webhook URL settings.

---

## Deployment (Docker)
This app includes a `Dockerfile` for effortless deployment to cloud platforms like Render, Railway, or Fly.io.

1. Build the image:
```bash
docker build -t prlens .
```
2. Run it (passing in all required environment variables):
```bash
docker run -p 8000:8000 --env-file .env prlens
```

---

## Project Structure
```text
PRLens/
├── app/
│   ├── config.py           # Environment variables configuration
│   ├── main.py             # FastAPI entrypoint
│   └── services/           
│       ├── diff_processor.py # Parses github diffs and filters lines
│       ├── github.py       # Interacts with the GitHub API
│       ├── llm.py          # Formats the brutal prompt and talks to OpenRouter
│       └── reviewer.py     # Orchestrates the review flow
├── tests/                  # Unit tests 
├── .env.example            # Sample config variables
├── Dockerfile              # Docker image definition
└── requirements.txt        # Python package dependencies
```

## Disclaimer
PRLens was explicitly engineered to be mean, nitpicky, and ruthless. Do not use it on a junior developer's PR unless you want them to cry. Use responsibly.
