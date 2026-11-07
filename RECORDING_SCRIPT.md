# Five-minute screen recording plan

Prepare before recording: activate `.venv`, install requirements, install Chromium, set `GROQ_API_KEY` in the PowerShell session, and confirm the Groq model is available. Do not show or read the API key on camera. Use Windows Game Bar (`Win+Alt+R`) or another screen recorder. Run the browser visibly (do not pass `--headless`).

Run both demonstrations with:

```powershell
python run.py --file tests/submission.txt --max-steps 12 --out reports/submission
```

## Timeline and narration

- **0:00–0:30 — Introduce the project.** Show the repo and say: “This takes a plain-English browser test, asks a Groq model for one action at a time, and executes those actions with Playwright. The same runner is used for both sites.”
- **0:30–1:10 — Explain the implementation.** Show `README.md` and `run.py`. Point out the observation, JSON action, validation, Playwright execution, assertion verification, and report generation. Mention that the selectors are derived from each page rather than hard-coded to either site.
- **1:10–2:10 — Run the PHPTravels case.** Start the command above and show the browser on the PHPTravels demo page. Explain that the current page asks visitors to request demo access, so the test checks the visible access form and button instead of claiming to log into an account it does not provide.
- **2:10–3:30 — Run the SauceDemo case.** Show the agent observing the page, using the demo credentials shown there, logging in, and checking that the Products page is visible.
- **3:30–4:20 — Show the reports.** Open `reports/submission/report.html`, then briefly show `report.json` and a step screenshot. Explain that PASS requires an assertion verified against the live page, not just a model claim.
- **4:20–5:00 — State limitations and close.** Mention model variability and cost, CAPTCHAs/MFA, inaccessible iframes or canvas controls, ambiguous tests, and missing credentials or test data. End by showing the two case results.

If a case fails during recording, keep the failure visible and explain the reported reason. Do not present a failed run as a pass; rerun after resolving its cause if time permits.
