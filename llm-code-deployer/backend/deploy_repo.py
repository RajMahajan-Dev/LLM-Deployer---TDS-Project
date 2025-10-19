# deploy_repo.py
import logging
import os
import subprocess
from pathlib import Path
from textwrap import dedent
from typing import Dict, Tuple, Optional

import certifi
import requests
from github import Github

logger = logging.getLogger("llm-deployer")

# Clear bad CA bundle environment variables before importing requests
for var in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
    p = os.environ.get(var)
    if p and not os.path.exists(p):
        os.environ.pop(var, None)

# Force requests to use certifi's CA bundle
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()


def _get_credentials() -> Tuple[str, str]:
    github_username = os.getenv("GITHUB_USERNAME")
    github_token = os.getenv("GITHUB_TOKEN")

    if os.getenv("DEBUG_GITHUB_CREDS") == "1":
        print(f"GitHub Username: {github_username}, Token set: {'yes' if github_token else 'no'}")

    if not github_username or not github_token:
        raise ValueError(
            f"GitHub credentials missing: USERNAME={github_username}, TOKEN={'set' if github_token else 'missing'}"
        )
    return github_username, github_token


def _run_git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            dedent(
                f"""
                Git command failed: git {' '.join(args)}
                stdout: {result.stdout.strip() or '<empty>'}
                stderr: {result.stderr.strip() or '<empty>'}
                """
            ).strip()
        )
    return result


def _handle_push_error(result: subprocess.CompletedProcess[str]) -> None:
    stderr = result.stderr.strip()
    if "Permission to" in stderr and "denied" in stderr:
        raise PermissionError(
            dedent(
                f"""
                GitHub rejected the push (permission denied). This usually happens when the PAT lacks the `repo` scope or
                is a fine-grained token that does not include newly created repositories.

                Please create a new Personal Access Token (classic) with at least the `repo` scope, update `GITHUB_TOKEN` in
                backend/.env, restart the server, and re-run the build.

                Original git error:
                {stderr}
                """
            ).strip()
        )
    raise RuntimeError(
        dedent(
            f"""
            git push failed.
            stdout: {result.stdout.strip() or '<empty>'}
            stderr: {stderr or '<empty>'}
            """
        ).strip()
    )


def _commit_if_needed(cwd: Path, message: str, paths: Optional[Tuple[str, ...]] = None) -> bool:
    if paths:
        _run_git(cwd, "add", *paths)
    else:
        _run_git(cwd, "add", ".")
    try:
        _run_git(cwd, "commit", "-m", message)
        return True
    except RuntimeError as err:
        if "nothing to commit" in str(err).lower():
            logger.info("No changes to commit for '%s'", message)
            return False
        raise


def _push_with_retry(cwd: Path, args: Tuple[str, ...], *, force_on_conflict: bool) -> None:
    result = _run_git(cwd, *args, check=False)
    if result.returncode == 0:
        return

    stderr = result.stderr.lower()
    if force_on_conflict and ("non-fast-forward" in stderr or "fetch first" in stderr):
        logger.info("Retrying git push with --force due to remote history")
        force_args = list(args)
        if "-f" not in force_args and "--force" not in force_args:
            force_args.insert(1, "-f")
        force_result = _run_git(cwd, *force_args, check=False)
        if force_result.returncode == 0:
            return
        _handle_push_error(force_result)

    _handle_push_error(result)


def _ensure_pages_site(username: str, repo_name: str, token: str) -> None:
    url = f"https://api.github.com/repos/{username}/{repo_name}/pages"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {token}",
    }
    payload = {"source": {"branch": "main", "path": "/"}}
    response = requests.post(url, json=payload, headers=headers, timeout=15)
    if response.status_code in (201, 204):
        logger.info("Enabled GitHub Pages for %s", repo_name)
    elif response.status_code == 409:
        logger.info("GitHub Pages already enabled for %s", repo_name)
    else:
        logger.warning("Could not enable GitHub Pages (%s): %s", response.status_code, response.text)


def _trigger_pages_build(username: str, repo_name: str, token: str) -> None:
    url = f"https://api.github.com/repos/{username}/{repo_name}/pages/builds"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {token}",
    }
    response = requests.post(url, headers=headers, timeout=15)
    if response.status_code not in (201, 202, 204):
        logger.warning("GitHub Pages build trigger failed (%s): %s", response.status_code, response.text)
    else:
        logger.info("Triggered GitHub Pages build for %s", repo_name)


def create_and_push_repo(local_dir: str, repo_name: str, private: bool = False) -> Dict[str, str]:
    """Create (if needed) and push a repository for Round 1."""

    github_username, github_token = _get_credentials()
    g = Github(github_token)
    try:
        user = g.get_user()  # type: ignore
        print(f"Authenticated as: {user.login}")  # type: ignore
    except Exception as e:
        raise ValueError(
            "GitHub token authentication failed. Generate a new classic token with `repo` scope at "
            "https://github.com/settings/tokens."
        ) from e

    repo = None
    created_repo = False
    try:
        repo = user.create_repo(repo_name, private=private, auto_init=False)  # type: ignore
        print(f"✅ Created new repo: {repo.full_name}")
        created_repo = True
    except Exception as e:
        print(f"⚠️  Repo may already exist, attempting to get it: {e}")
        try:
            repo = user.get_repo(repo_name)  # type: ignore
            print(f"✅ Using existing repo: {repo.full_name}")
        except Exception as e2:
            raise Exception(f"Failed to create or access repo '{repo_name}': {e2}")

    if repo.private:  # ensure public per requirements
        try:
            repo.edit(private=False)  # type: ignore
            print("ℹ️  Repository made public")
        except Exception as e:
            print(f"⚠️  Could not flip repo to public: {e}")

    cwd = Path(local_dir).resolve()

    _run_git(cwd, "init")
    _run_git(cwd, "checkout", "-B", "main")

    remote_url = f"https://{github_username}:{github_token}@github.com/{github_username}/{repo_name}.git"

    try:
        _run_git(cwd, "remote", "remove", "origin")
    except RuntimeError:
        pass
    _run_git(cwd, "remote", "add", "origin", remote_url)

    workflow_committed = _commit_if_needed(cwd, "Configure GitHub Pages workflow", (".github",))
    if workflow_committed:
        _push_with_retry(cwd, ("push", "-u", "origin", "main"), force_on_conflict=True)

    content_committed = _commit_if_needed(cwd, "Round 1 scaffold")
    if content_committed or not workflow_committed:
        _push_with_retry(cwd, ("push", "-u", "origin", "main"), force_on_conflict=True)

    commit_sha = _run_git(cwd, "rev-parse", "HEAD").stdout.strip()

    _ensure_pages_site(github_username, repo_name, github_token)
    _trigger_pages_build(github_username, repo_name, github_token)

    pages_url = f"https://{github_username}.github.io/{repo_name}/"
    print(f"🚀 Deployed to: {pages_url}")

    return {
        "repo_url": repo.html_url,  # type: ignore
        "pages_url": pages_url,
        "commit_sha": commit_sha,
    }


def push_existing_repo(local_dir: str, repo_name: str, commit_message: str) -> Dict[str, str]:
    """Commit and push updates to an existing repository for Round 2."""

    github_username, github_token = _get_credentials()
    cwd = Path(local_dir).resolve()
    if not (cwd / ".git").exists():
        raise RuntimeError(f"No git repository found at {cwd}")

    remote_url = f"https://{github_username}:{github_token}@github.com/{github_username}/{repo_name}.git"

    remotes = _run_git(cwd, "remote").stdout.splitlines()
    if "origin" not in remotes:
        _run_git(cwd, "remote", "add", "origin", remote_url)
    else:
        _run_git(cwd, "remote", "set-url", "origin", remote_url)

    changes_committed = _commit_if_needed(cwd, commit_message)
    commit_sha = _run_git(cwd, "rev-parse", "HEAD").stdout.strip()

    if changes_committed:
        push_result = _run_git(cwd, "push", "origin", "main", check=False)
        if push_result.returncode != 0:
            _handle_push_error(push_result)
    else:
        logger.info("No changes to push for repo %s", repo_name)

    _ensure_pages_site(github_username, repo_name, github_token)
    pages_url = f"https://{github_username}.github.io/{repo_name}/"
    repo_url = f"https://github.com/{github_username}/{repo_name}"

    _trigger_pages_build(github_username, repo_name, github_token)

    return {
        "repo_url": repo_url,
        "pages_url": pages_url,
        "commit_sha": commit_sha,
    }
