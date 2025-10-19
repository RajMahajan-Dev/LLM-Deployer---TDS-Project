# LLM Code Deployer

Automated code generation and deployment system using LLMs (via aipipe.org) and GitHub Pages.

## Features

- ✅ Generate HTML/CSS/JS apps using OpenAI GPT-4o-mini via aipipe.org
- ✅ Automatic GitHub repository creation
- ✅ Automatic GitHub Pages deployment
- ✅ Clean HTML extraction (removes markdown and explanations)
- ✅ Windows-compatible file paths
- ✅ TLS certificate handling for Python 3.13

## Setup

### 1. Install Dependencies

```powershell
# From project root with venv activated
cd D:\tds-proj1\llm-code-deployer\backend
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Edit `backend/.env`:

```properties
OPENAI_API_KEY=your_aipipe_token_here
GITHUB_USERNAME=your_github_username
GITHUB_TOKEN=your_github_personal_access_token
STUDENT_SECRET=your_secret_key
```

**Important:**
- Get aipipe token from: https://aipipe.org/login
- Generate GitHub token: https://github.com/settings/tokens (with `repo` scope)
- No inline comments in `.env` file!

### 3. Fix CA Certificate Issues (Windows Only)

If you see TLS/SSL errors, clear these environment variables:

```powershell
$env:CURL_CA_BUNDLE=$null
$env:SSL_CERT_FILE=$null
$env:REQUESTS_CA_BUNDLE=$null
```

## Running the Server

### Option 1: Manual Start

```powershell
cd D:\tds-proj1\llm-code-deployer\backend

# Clear CA env vars (important!)
$env:CURL_CA_BUNDLE=$null
$env:SSL_CERT_FILE=$null
$env:REQUESTS_CA_BUNDLE=$null

# Activate venv
D:\tds-proj1\.venv\Scripts\Activate.ps1

# Start server
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

### Option 2: Use Start Script

```powershell
.\start_server.ps1
```

## API Usage

### Build & Deploy Endpoint

```powershell
POST http://127.0.0.1:8000/build
Content-Type: application/json

{
  "student": "username",
  "brief": "Description of the app to generate",
  "secret": "your_secret_key"
}
```

**PowerShell Example:**

```powershell
$env:CURL_CA_BUNDLE=$null
$env:SSL_CERT_FILE=$null
$env:REQUESTS_CA_BUNDLE=$null

Invoke-RestMethod -Uri "http://127.0.0.1:8000/build" -Method POST `
  -Headers @{ "Content-Type" = "application/json" } `
  -Body '{"student":"myname","brief":"A todo list app","secret":"rajsecret2151"}' `
  -TimeoutSec 180
```

**Response:**

```json
{
  "status": "deployed",
  "repo": "myname-app",
  "pages_url": "https://your-username.github.io/myname-app/"
}
```

## Testing

Use the provided test script:

```powershell
.\test_build.ps1
```

## Project Structure

```
llm-code-deployer/
├── backend/
│   ├── .env                 # Environment variables
│   ├── main.py             # FastAPI application
│   ├── generator.py        # LLM code generation
│   ├── deploy_repo.py      # GitHub deployment
│   └── requirements.txt    # Python dependencies
├── frontend/
│   └── index.html          # Frontend UI
├── test_build.ps1          # Test script
└── README.md              # This file
```

## Key Updates

### 1. Fixed API Endpoint
- ❌ Old: `https://api.aipipe.org` (doesn't exist)
- ✅ New: `https://aipipe.org/openrouter/v1/chat/completions`

### 2. Improved HTML Extraction
- Removes markdown code blocks (```html...```)
- Extracts only HTML content
- Handles LLM explanations

### 3. GitHub Pages Auto-Enable
- Automatically enables Pages after deployment
- Uses `main` branch as source

### 4. Better Error Handling
- Validates GitHub credentials
- Handles existing repositories
- Force push to update content

### 5. Windows Compatibility
- Uses `tempfile.gettempdir()` instead of `/tmp`
- Handles CA certificate bundle issues

## Troubleshooting

### Server won't start
```powershell
# Make sure you're in backend folder
cd D:\tds-proj1\llm-code-deployer\backend

# Check Python path
python --version  # Should be 3.13

# Reinstall uvicorn
pip install uvicorn --force-reinstall
```

### TLS Certificate Errors
```powershell
# Clear bad environment variables
$env:CURL_CA_BUNDLE=$null
$env:SSL_CERT_FILE=$null
$env:REQUESTS_CA_BUNDLE=$null

# Reinstall certifi
pip install certifi --upgrade
```

### GitHub Authentication Fails
1. Verify token at: https://github.com/settings/tokens
2. Ensure token has `repo` scope
3. Generate new token if expired
4. Update `.env` file (no comments!)

### Generated HTML is wrapped in code blocks
The updated `generator.py` now:
- Uses stricter prompts
- Extracts HTML using regex patterns
- Removes markdown formatting

## License

MIT

## Credits

- OpenAI GPT models via aipipe.org
- GitHub API via PyGithub
- FastAPI framework
