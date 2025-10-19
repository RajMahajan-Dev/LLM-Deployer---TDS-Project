import os
import json
import re
import base64
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, unquote

import requests
import certifi


def generate_simple_static_app(
    brief: str,
    output_dir: str,
    *,
    task: Optional[str] = None,
    round_number: int = 1,
    attachments: Optional[List[Dict[str, Any]]] = None,
):
    """
    Generates a simple static app using aipipe.org's LLM API
    and writes HTML, CSS, and JS files into output_dir.
    """
    aipipe_api_url = "https://aipipe.org/openrouter/v1/chat/completions"  # Aipipe endpoint (via OpenRouter)
    aipipe_api_key = os.getenv("OPENAI_API_KEY")  # your key from aipipe.org

    if not aipipe_api_key:
        raise ValueError("Missing API key. Please set OPENAI_API_KEY environment variable.")

    headers = {
        "Authorization": f"Bearer {aipipe_api_key}",
        "Content-Type": "application/json",
    }

    attachments_info = _download_attachments(attachments or [], output_dir)
    attachments_prompt = _build_attachments_prompt(attachments_info)

    round_context = "You are creating the initial version of this project." if round_number == 1 else (
        "You are updating an existing project. Apply the new requirements while preserving useful structure. Replace outdated content when necessary."
    )

    metadata_lines = [
        f"Task: {task or 'unspecified'}",
        f"Round: {round_number}",
    ]

    user_prompt = (
        f"Project brief:\n{brief.strip()}\n\n"
        f"Context:\n- {round_context}\n"
        + "\n".join(f"- {line}" for line in metadata_lines)
        + (
            "\n\n" + attachments_prompt if attachments_prompt else ""
        )
        + "\n\nDeliver a single HTML file that:\n"
          "- Starts with <!DOCTYPE html> and includes <html>, <head>, and <body>.\n"
          "- Embeds all CSS inside <style> tags and scripts inside <script> tags.\n"
          "- Uses relative paths when loading any downloaded attachment (e.g., ./assets/filename).\n"
          "- Provides graceful error handling for network fetches.\n"
          "- Includes thoughtful, mobile-friendly design."
    )

    payload = {
        "model": "openai/gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert frontend developer. Generate ONLY the complete HTML code with embedded CSS and "
                    "JavaScript. DO NOT include explanations, markdown formatting, or code fences."
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
    }

    # Ensure we don't inherit a broken CA bundle path from the environment (e.g., Python312)
    for var in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
        p = os.environ.get(var)
        if p and not os.path.exists(p):
            # Remove bad override so requests can use certifi
            os.environ.pop(var, None)

    # Always use certifi's CA bundle to avoid OS/env misconfiguration
    try:
        response = requests.post(
            aipipe_api_url,
            headers=headers,
            data=json.dumps(payload),
            verify=certifi.where(),
            timeout=60,
        )
    except Exception as e:
        raise Exception(f"API request failed (network/TLS): {e}")

    if response.status_code != 200:
        raise Exception(f"API request failed: {response.text}")

    data = response.json()
    generated_code = data["choices"][0]["message"]["content"]

    # Extract HTML from markdown code blocks if present
    generated_code = _extract_html_from_response(generated_code)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_file = Path(output_dir) / "index.html"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(generated_code)

    print(f"✅ App generated successfully in {output_file}")
    return True


def _extract_html_from_response(content: str) -> str:
    """
    Extract clean HTML from LLM response.
    Removes markdown code blocks, explanations, and extra text.
    """
    # Remove markdown code blocks (```html ... ``` or ``` ... ```)
    # Pattern: ```html or ``` followed by content, ending with ```
    code_block_pattern = r'```(?:html)?\s*\n?(.*?)```'
    matches = re.findall(code_block_pattern, content, re.DOTALL | re.IGNORECASE)
    
    if matches:
        # Use the first code block found
        content = matches[0].strip()
    
    # If no code blocks, try to find HTML by looking for <!DOCTYPE or <html
    if not content.strip().startswith('<!DOCTYPE') and not content.strip().startswith('<html'):
        # Try to extract from <!DOCTYPE to </html>
        html_pattern = r'(<!DOCTYPE[^>]*>.*?</html>)'
        html_matches = re.findall(html_pattern, content, re.DOTALL | re.IGNORECASE)
        if html_matches:
            content = html_matches[0]
        else:
            # Try just <html to </html>
            html_pattern2 = r'(<html[^>]*>.*?</html>)'
            html_matches2 = re.findall(html_pattern2, content, re.DOTALL | re.IGNORECASE)
            if html_matches2:
                content = html_matches2[0]
    
    return content.strip()


def _download_attachments(raw_attachments: List[Dict[str, Any]], output_dir: str) -> List[Dict[str, Any]]:
    assets_dir = Path(output_dir) / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    downloaded: List[Dict[str, Any]] = []

    for idx, attachment in enumerate(raw_attachments):
        url = str(attachment.get("url", "")).strip()
        if not url:
            continue

        name_hint = attachment.get("name") or _derive_name_from_url(url, idx)
        safe_name = _sanitize_filename(name_hint)
        dest_path = assets_dir / safe_name
        dest_path = _ensure_unique_path(dest_path)

        try:
            data, content_type = _fetch_attachment_bytes(url)
        except Exception as exc:
            print(f"⚠️  Failed to download attachment {name_hint}: {exc}")
            continue

        dest_path.write_bytes(data)

        preview_text = None
        if _is_text_like(content_type, dest_path):
            try:
                preview_text = dest_path.read_text(encoding="utf-8")[:1200]
            except Exception:
                preview_text = None

        downloaded.append(
            {
                "name": attachment.get("name") or safe_name,
                "relative_path": str(dest_path.relative_to(output_dir)).replace("\\", "/"),
                "content_type": content_type,
                "bytes": len(data),
                "preview": preview_text,
            }
        )

    return downloaded


def _fetch_attachment_bytes(url: str) -> tuple[bytes, str]:
    if url.startswith("data:"):
        header, _, data = url.partition(",")
        if not data:
            raise ValueError("Invalid data URI")
        media_type = "application/octet-stream"
        if ";" in header:
            media_type = header.split(";")[0][5:] or media_type
        if ";base64" in header:
            return base64.b64decode(data), media_type
        return unquote(data).encode("utf-8"), media_type

    response = requests.get(url, timeout=45, verify=certifi.where())
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code} while downloading {url}")
    content_type = response.headers.get("Content-Type", "application/octet-stream").split(";")[0]
    return response.content, content_type


def _derive_name_from_url(url: str, idx: int) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name
    if name:
        return name
    return f"attachment-{idx + 1}"


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", name.strip())
    return cleaned or "attachment"


def _ensure_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _is_text_like(content_type: str, path: Path) -> bool:
    if content_type.startswith("text/"):
        return True
    return path.suffix.lower() in {".csv", ".md", ".json", ".txt", ".tsv"}


def _build_attachments_prompt(attachments: List[Dict[str, Any]]) -> str:
    if not attachments:
        return ""

    lines: List[str] = [
        "Attachments are available in the ./assets directory (relative to index.html). Use fetch('./assets/<name>') or <img src='./assets/<name>'> as needed.",
        "Attachment details:",
    ]

    for item in attachments:
        line = f"- {item['relative_path']} ({item['content_type']}, {item['bytes']} bytes)"
        if item.get("preview"):
            preview = item["preview"].strip()
            preview = preview[:600]
            line += f"\n  Preview snippet:\n  {preview}"
        lines.append(line)

    return "\n".join(lines)
