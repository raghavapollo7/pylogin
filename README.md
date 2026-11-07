# Plain-English Browser Tester

A small Python + Playwright CLI that asks a Groq-hosted model to observe a page and choose one browser action at a time. It is site-agnostic: the model receives visible page text and interactive controls instead of site-specific selectors.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
$env:GROQ_API_KEY = "your-groq-api-key"
# Optional; defaults to openai/gpt-oss-120b
$env:GROQ_MODEL = "your-enabled-groq-model"
```

## Run

```powershell
python run.py --url https://phptravels.com/demo --test "Log in as a customer and verify the dashboard is shown"
python run.py --url https://www.saucedemo.com/ --test "Log in and verify the products page is shown" --headless
python run.py --file tests/sample.txt --headless --max-steps 25
python run.py --url https://example.com --test "Search for a product" --vars query=backpack
```

`--file` accepts one case per line in `URL | plain English test` format. Blank lines and lines beginning with `#` are ignored. Repeat `--vars KEY=VALUE` for multiple values. Create a key in the [Groq Console](https://console.groq.com/keys). Model configuration uses `GROQ_MODEL`; the key is read from `GROQ_API_KEY`. The default model is `openai/gpt-oss-120b`. Each run writes `reports/report.json`, `reports/report.html`, and a screenshot for each observed step under `reports/case_NN/`. Exit status is nonzero if any test fails.

If the API returns HTTP 403, check that the selected model is enabled for your Groq project and organization in the Groq Console, or set `GROQ_MODEL` to a model your account can access. A 401 usually indicates an invalid or incorrectly copied API key.

The Groq API call uses the official `groq` Python package. If you previously installed dependencies, refresh them with `pip install -r requirements.txt` before rerunning.

## Architecture

```text
CLI test + vars
      |
      v
Playwright opens URL ----> observe visible text + controls + URL/title
      |                                      |
      |                                      v
      |                              Groq JSON action
      |                                      |
      +<------ execute with timeout <---------+
                     |
             result/error feedback -> next observation
                     |
       code-verified assertions + step screenshots
                     |
           PASS/FAIL + JSON and HTML reports
```

Each action is validated against a strict schema. Malformed model output is retried once. A PASS is accepted only after the runner itself verifies at least one visible-text, URL, or visibility assertion. Repeated identical actions trigger a loop guard. Generic cookie acceptance is attempted for common button labels.

## Sample cases

`tests/sample.txt` contains three PHPTravels demo scenarios and two SauceDemo scenarios. Credentials are not embedded in the runner; the model is instructed to use values supplied in the test, page, or `--vars`.

## Limitations

- LLM output can be inconsistent; retry, feedback, and loop limits reduce but do not remove flakiness.
- Each action cycle uses API tokens and incurs model cost. Check the model provider's current pricing.
- CAPTCHA, MFA, bot checks, inaccessible iframes, canvas-only interfaces, and file chooser flows may block automation.
- Ambiguous tests may produce an incorrect interpretation; write observable expected outcomes.
- The runner follows newly opened tabs after an action. JavaScript dialogs and complex popup consent flows may still need a future extension.
- The observation is intentionally compact and limited to visible text and interactive elements; content hidden behind scrolling may not be exposed.
- Do not use this against sites without authorization. Use demo accounts and avoid destructive actions.
