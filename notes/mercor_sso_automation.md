# Mercor SSO Automation Notes (Feb 2026)

## Problem

Google SSO frequently blocks Playwright-style automated browsers with messages like "This browser or app may not be secure", which prevents fully automated login flows.

## Working Approach

Use a normal Chromium-based browser instance (Comet) with a Chrome DevTools Protocol (CDP) port, then drive it with `agent-browser connect`.

High-level flow:

1. Launch Comet with a CDP port:

```bash
open -na "/Applications/Comet.app" --args \
  --remote-debugging-port=9222 \
  --no-first-run \
  --no-default-browser-check
```

2. Connect with agent-browser:

```bash
agent-browser --session mercor connect 9222
agent-browser --session mercor open https://work.mercor.com/login
```

3. Complete Google account chooser + consent screens inside the Comet instance.

4. Proceed with Mercor applications normally, capturing evidence screenshots to:

`Resume/applications/mercor/submissions/`

## Operational Notes

- Avoid persisting secrets or one-time login links in the repo.
- Mercor application steps are often reusable across roles (resume, availability, work authorization, interviews).
- Work Authorization typically requires Date of Birth; do not proceed without user-provided DOB.

