import os
import json
import re
import requests
import certifi
from pathlib import Path

def generate_simple_static_app(brief: str, output_dir: str):
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
        "Content-Type": "application/json"
    }

    payload = {
        "model": "openai/gpt-4o-mini",   # OpenRouter model format
        "messages": [
            {"role": "system", "content": "You are an expert frontend developer. Generate ONLY the complete HTML code with embedded CSS and JavaScript. DO NOT include any explanations, markdown formatting, or code block markers like ```html. Return ONLY the raw HTML starting with <!DOCTYPE html>."},
            {"role": "user", "content": f"Create a single-file HTML app for: {brief}\n\nRequirements:\n- Complete, valid HTML5\n- Embedded CSS in <style> tags\n- Embedded JavaScript in <script> tags\n- Mobile responsive\n- Modern, clean design\n- Return ONLY the HTML code, nothing else"}
        ],
        "temperature": 0.3
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
