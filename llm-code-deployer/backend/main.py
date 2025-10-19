# main.py
import json
import logging
import os
import re
import stat
import subprocess
import shutil
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from dotenv import load_dotenv
from deploy_repo import create_and_push_repo, push_existing_repo
from generator import generate_simple_static_app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm-deployer")

BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / "state.json"
STATE_LOCK = threading.Lock()

# Load .env relative to this file so uvicorn can run from any working directory
load_dotenv(dotenv_path=BASE_DIR / ".env")

STUDENT_SECRET = os.getenv("STUDENT_SECRET")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")

MIT_TEMPLATE = """MIT License

Copyright (c) {year} {owner}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


class BuildRequest(BaseModel):
    secret: str
    brief: str
    email: str
    task: str
    nonce: str
    round: int = Field(1, ge=1)
    evaluation_url: str
    student: Optional[str] = None
    attachments: Optional[Any] = None


app = FastAPI(title="LLM Code Deployer")


def _load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"tasks": {}}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        logger.exception("Failed to read state file; starting fresh")
        return {"tasks": {}}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _safe_rmtree(path: Path) -> None:
    """Remove directory trees on Windows even if files are read-only."""

    def _onerror(func, value, exc_info):
        if isinstance(exc_info[1], PermissionError):
            os.chmod(value, stat.S_IWRITE)
            func(value)
        else:
            raise exc_info[1]

    if path.exists():
        shutil.rmtree(path, onerror=_onerror)


def _slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "project"


def _predict_repo_urls(repo_name: str) -> Dict[str, str]:
    if not GITHUB_USERNAME:
        raise RuntimeError("GITHUB_USERNAME is not configured")
    return {
        "repo_url": f"https://github.com/{GITHUB_USERNAME}/{repo_name}",
        "pages_url": f"https://{GITHUB_USERNAME}.github.io/{repo_name}/",
    }


def _write_license(local_dir: Path, owner: str) -> None:
    license_path = local_dir / "LICENSE"
    license_path.write_text(MIT_TEMPLATE.format(year=datetime.utcnow().year, owner=owner or "Maintainer"))


def _write_pages_workflow(local_dir: Path) -> None:
    workflows_dir = local_dir / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = workflows_dir / "pages.yml"
    workflow_path.write_text(
        """name: Deploy static site\n\n"
        "on:\n"
        "  push:\n"
        "    branches: [\"main\"]\n"
        "  workflow_dispatch:\n\n"
        "permissions:\n"
        "  contents: read\n"
        "  pages: write\n"
        "  id-token: write\n\n"
        "concurrency:\n"
        "  group: \"pages\"\n"
        "  cancel-in-progress: false\n\n"
        "jobs:\n"
        "  deploy:\n"
        "    environment:\n"
        "      name: github-pages\n"
        "      url: ${{ steps.deployment.outputs.page_url }}\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: Checkout\n"
        "        uses: actions/checkout@v4\n"
        "      - name: Setup Pages\n"
        "        uses: actions/configure-pages@v5\n"
        "      - name: Upload artifact\n"
        "        uses: actions/upload-pages-artifact@v3\n"
        "        with:\n"
        "          path: .\n"
        "      - name: Deploy to GitHub Pages\n"
        "        id: deployment\n"
        "        uses: actions/deploy-pages@v4\n"
        """
    )


def _write_readme(local_dir: Path, req: BuildRequest, repo_name: str, pages_url: str) -> None:
    summary = req.brief.strip() or "Generated static site"
    content = f"""# {repo_name}

## Summary
{summary}

## Setup
1. Clone the repository.
2. Open `index.html` in a modern browser or serve via any static host.

## Usage
- Visit {pages_url}
- Update `index.html` to iterate quickly, then push changes to redeploy GitHub Pages.

## Code Explanation
This project was generated automatically from the brief provided in Round {req.round}. The `index.html` file contains a self-contained static experience. Supporting files such as the MIT license and README are managed programmatically to keep history clean.

## License
Released under the MIT License. See `LICENSE` for details.
"""
    (local_dir / "README.md").write_text(content)


def _write_nojekyll(local_dir: Path) -> None:
    (local_dir / ".nojekyll").write_text("")


def _write_static_entrypoint(local_dir: Path) -> None:
        html_path = local_dir / "index.html"
        if not html_path.exists():
                html_path.write_text(
                        """<!DOCTYPE html>
<html lang=\"en\">
    <head>
        <meta charset=\"UTF-8\" />
        <title>Site under construction</title>
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
        <style>
            body { font-family: system-ui, sans-serif; display: grid; place-items: center; min-height: 100vh; margin: 0; background: #f4f6fb; color: #1f2937; }
            main { text-align: center; padding: 2rem; max-width: 640px; }
            h1 { font-size: clamp(2rem, 5vw, 3.5rem); margin-bottom: 1rem; }
            p { line-height: 1.6; }
            code { background: rgba(0,0,0,0.08); padding: 0.2rem 0.4rem; border-radius: 4px; }
        </style>
    </head>
    <body>
        <main>
            <h1>Deployment in progress…</h1>
            <p>The automated builder is preparing this project. Refresh in a moment to see the live site.</p>
            <p>If you own this repo, make sure pushes reach the <code>main</code> branch and GitHub Pages is enabled.</p>
        </main>
    </body>
</html>
"""
                )


def _prepare_local_dir(repo_name: str, *, create: bool) -> Path:
    temp_base = Path(tempfile.gettempdir()) / "llm-deployer"
    temp_base.mkdir(parents=True, exist_ok=True)
    local_dir = temp_base / repo_name
    _safe_rmtree(local_dir)
    if create:
        local_dir.mkdir(parents=True, exist_ok=True)
    return local_dir


def _post_evaluation(req: BuildRequest, repo_info: Dict[str, str], round_number: int) -> None:
    payload = {
        "email": req.email,
        "task": req.task,
        "round": round_number,
        "nonce": req.nonce,
        "repo_url": repo_info.get("repo_url"),
        "commit_sha": repo_info.get("commit_sha"),
        "pages_url": repo_info.get("pages_url"),
    }
    backoff = 1
    for attempt in range(5):
        try:
            resp = requests.post(req.evaluation_url, json=payload, timeout=15)
            if resp.status_code == 200:
                logger.info("Evaluation callback succeeded (round %s)", round_number)
                return
            logger.warning("Evaluation callback failed (%s): %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.warning("Evaluation callback error (attempt %s): %s", attempt + 1, exc)
        time.sleep(backoff)
        backoff *= 2
    logger.error("Failed to notify evaluation service after retries")


def _clone_repo(repo_name: str, destination: Path) -> None:
    if not GITHUB_USERNAME:
        raise RuntimeError("GITHUB_USERNAME is not configured")
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        raise RuntimeError("GITHUB_TOKEN is not configured")
    remote = f"https://{GITHUB_USERNAME}:{github_token}@github.com/{GITHUB_USERNAME}/{repo_name}.git"
    result = subprocess.run(["git", "clone", remote, str(destination)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed: {result.stderr.strip() or result.stdout.strip()}")


def _wait_for_pages(pages_url: str, timeout_seconds: int = 240, interval_seconds: int = 8) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            resp = requests.get(pages_url, timeout=10, headers={"Cache-Control": "no-cache"})
            if resp.status_code == 200:
                logger.info("GitHub Pages healthy at %s", pages_url)
                return True
            logger.info("Waiting for GitHub Pages (%s): HTTP %s", pages_url, resp.status_code)
        except Exception as exc:
            logger.info("Waiting for GitHub Pages (%s) due to %s", pages_url, exc)
        time.sleep(interval_seconds)
    logger.warning("GitHub Pages still returning non-200 after %s seconds", timeout_seconds)
    return False


def _process_round_one(req: BuildRequest) -> None:
    task_slug = _slugify(req.task)
    repo_name = f"{task_slug}-{req.nonce[:6].lower()}"
    logger.info("Round 1 start for task=%s repo=%s", req.task, repo_name)

    local_dir = _prepare_local_dir(repo_name, create=True)
    generated = generate_simple_static_app(req.brief, str(local_dir))
    if not generated:
        raise RuntimeError("LLM generation failed")

    predicted_urls = _predict_repo_urls(repo_name)
    _write_license(local_dir, req.email)
    _write_pages_workflow(local_dir)
    _write_readme(local_dir, req, repo_name, predicted_urls["pages_url"])
    _write_nojekyll(local_dir)
    _write_static_entrypoint(local_dir)

    repo_info = create_and_push_repo(str(local_dir), repo_name, private=False)
    pages_ready = _wait_for_pages(repo_info["pages_url"])
    if not pages_ready:
        raise RuntimeError(f"GitHub Pages did not return HTTP 200 for {repo_info['pages_url']}")

    with STATE_LOCK:
        state = _load_state()
        state.setdefault("tasks", {})[task_slug] = {
            "repo_name": repo_name,
            "repo_url": repo_info["repo_url"],
            "pages_url": repo_info["pages_url"],
            "last_commit_sha": repo_info["commit_sha"],
            "email": req.email,
            "nonce": req.nonce,
            "evaluation_url": req.evaluation_url,
            "pages_ready": pages_ready,
        }
        _save_state(state)

    _post_evaluation(req, repo_info, round_number=1)


def _process_round_two(req: BuildRequest) -> None:
    task_slug = _slugify(req.task)
    with STATE_LOCK:
        state = _load_state()
        task_state = state.get("tasks", {}).get(task_slug)
    if not task_state:
        raise RuntimeError(f"No Round 1 state found for task '{req.task}'")

    repo_name = task_state["repo_name"]
    logger.info("Round 2 start for task=%s repo=%s", req.task, repo_name)

    local_dir = _prepare_local_dir(repo_name, create=False)
    _clone_repo(repo_name, local_dir)

    generated = generate_simple_static_app(req.brief, str(local_dir))
    if not generated:
        raise RuntimeError("LLM generation failed in round 2")

    predicted_urls = _predict_repo_urls(repo_name)
    _write_license(local_dir, req.email)
    _write_pages_workflow(local_dir)
    _write_readme(local_dir, req, repo_name, predicted_urls["pages_url"])
    _write_nojekyll(local_dir)
    _write_static_entrypoint(local_dir)

    repo_info = push_existing_repo(str(local_dir), repo_name, commit_message="Round 2 update")
    pages_ready = _wait_for_pages(repo_info["pages_url"])
    if not pages_ready:
        raise RuntimeError(f"GitHub Pages did not return HTTP 200 for {repo_info['pages_url']}")

    with STATE_LOCK:
        state = _load_state()
        state.setdefault("tasks", {})[task_slug] = {
            **task_state,
            "pages_url": repo_info["pages_url"],
            "repo_url": repo_info["repo_url"],
            "last_commit_sha": repo_info["commit_sha"],
            "pages_ready": pages_ready,
        }
        _save_state(state)

    _post_evaluation(req, repo_info, round_number=2)


def _process_request(payload: Any) -> None:
    req = payload if isinstance(payload, BuildRequest) else BuildRequest(**payload)
    try:
        if req.round == 1:
            _process_round_one(req)
        elif req.round == 2:
            _process_round_two(req)
        else:
            logger.warning("Unsupported round: %s", req.round)
    except Exception:
        logger.exception("Processing failed for task=%s round=%s", req.task, req.round)


@app.post("/build")
async def build(req: BuildRequest, background_tasks: BackgroundTasks):
    if req.secret != STUDENT_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    background_tasks.add_task(_process_request, req.dict())
    return {"status": "accepted", "message": "Processing in background"}


@app.post("/evaluate")
async def evaluate(body: Dict[str, Any]):
    return {"received": body, "message": "Evaluation stored (simulated)"}
