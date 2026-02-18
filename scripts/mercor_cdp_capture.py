#!/usr/bin/env python3
"""
Capture Mercor application status evidence from a Comet/Chrome instance
running with --remote-debugging-port (CDP).

Why: Playwright CDP attach can hang on some Chromium forks; this script uses
raw CDP over WebSocket to navigate and capture screenshots.

Safety:
- Does not persist page text to disk (only screenshots).
- Avoid running this while Work Authorization (DOB) fields are visible.
"""

from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.request
from pathlib import Path

import asyncio
import websockets


def _json_get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


def _pick_page_target(port: int, prefer_host: str) -> dict:
    targets = _json_get(f"http://localhost:{port}/json/list")
    # Prefer an existing tab already on the site (avoids creating new tabs).
    for t in targets:
        u = (t.get("url") or "").lower()
        if prefer_host.lower() in u:
            return t
    # Fallback: pick any non-empty page.
    for t in targets:
        if (t.get("webSocketDebuggerUrl") or "") and (t.get("url") or ""):
            return t
    raise RuntimeError("No usable CDP page targets found. Keep at least one tab open.")


async def _cdp_capture(
    ws_url: str,
    url: str,
    out_path: Path,
    *,
    timeout_s: int,
    click_texts: list[str] | None = None,
    wait_any: list[str] | None = None,
) -> str:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    msg_id = 0

    async with websockets.connect(
        ws_url,
        max_size=32 * 1024 * 1024,
        ping_interval=10,
        ping_timeout=30,
    ) as ws:
        async def send(method: str, params: dict | None = None):
            nonlocal msg_id
            msg_id += 1
            payload = {"id": msg_id, "method": method}
            if params:
                payload["params"] = params
            await ws.send(json.dumps(payload))
            return msg_id

        async def recv_until(*, want_id: int, timeout_s: int) -> dict:
            start = time.time()
            while True:
                remaining = max(0.5, timeout_s - int(time.time() - start))
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                msg = json.loads(raw)
                if msg.get("id") == want_id:
                    return msg
                # Ignore unrelated responses/events.

        # Enable essentials.
        await send("Page.enable")
        await send("Runtime.enable")
        await send("Network.enable")

        nav_id = await send("Page.navigate", {"url": url})

        # Wait for basic load event, but don't trust it for SPAs.
        start = time.time()
        saw_load = False
        while True:
            if time.time() - start > timeout_s:
                break
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
            msg = json.loads(raw)
            if msg.get("id") == nav_id:
                continue
            if msg.get("method") == "Page.loadEventFired":
                saw_load = True
                break

        # Wait for content to actually render (React/SPA can render after load event).
        # We poll for a non-trivial body text length.
        start = time.time()
        body_text = ""
        while True:
            if time.time() - start > timeout_s:
                break
            eval_id = await send(
                "Runtime.evaluate",
                {
                    "expression": "({rs: document.readyState, t: (document.body && document.body.innerText) ? document.body.innerText : ''})",
                    "returnByValue": True,
                },
            )
            msg = await recv_until(want_id=eval_id, timeout_s=timeout_s)
            val = (((msg.get("result") or {}).get("result") or {}).get("value") or {})
            body_text = (val.get("t") or "")
            rs = (val.get("rs") or "")
            if rs == "complete" and len(body_text.strip()) >= 200:
                break
            await asyncio.sleep(0.5)

        # Bring tab to front before screenshot (helps on some Chromium forks).
        await send("Page.bringToFront")
        await asyncio.sleep(0.8)

        # (Re-)Extract body text for status hints (not persisted).
        eval_id2 = await send(
            "Runtime.evaluate",
            {
                "expression": "document.body ? document.body.innerText : ''",
                "returnByValue": True,
            },
        )
        msg = await recv_until(want_id=eval_id2, timeout_s=timeout_s)
        body_text = (((msg.get("result") or {}).get("result") or {}).get("value") or "")

        # Optional clicks (e.g., "Submit Application" then "Apply anyway").
        click_texts = click_texts or []
        for click_button_text in click_texts:
            click_expr = (
                "(function(){"
                "const needle = " + json.dumps(click_button_text.lower()) + ";"
                "const btns = Array.from(document.querySelectorAll('button'));"
                "const b = btns.find(x => (x.innerText||'').toLowerCase().includes(needle));"
                "if(!b) return {clicked:false, reason:'not_found', needle};"
                "try{ b.scrollIntoView({block:'center'}); }catch(e){}"
                "b.click();"
                "return {clicked:true, needle};"
                "})()"
            )
            await send(
                "Runtime.evaluate",
                {"expression": click_expr, "returnByValue": True, "awaitPromise": False},
            )
            await asyncio.sleep(1.2)

        # Wait for any of the target substrings after clicks (best-effort).
        wait_any = [w for w in (wait_any or []) if w.strip()]
        if wait_any:
            needles = [w.lower() for w in wait_any]
            start = time.time()
            while True:
                if time.time() - start > timeout_s:
                    break
                eval_idw = await send(
                    "Runtime.evaluate",
                    {
                        "expression": "document.body ? document.body.innerText : ''",
                        "returnByValue": True,
                    },
                )
                msg = await recv_until(want_id=eval_idw, timeout_s=timeout_s)
                cur = (((msg.get("result") or {}).get("result") or {}).get("value") or "")
                cur_l = cur.lower()
                if any(n in cur_l for n in needles):
                    body_text = cur
                    break
                await asyncio.sleep(0.8)

        # Screenshot (viewport).
        shot_id = await send("Page.captureScreenshot", {"format": "png"})
        b64 = ""
        msg = await recv_until(want_id=shot_id, timeout_s=timeout_s)
        b64 = (msg.get("result") or {}).get("data") or ""

        if not b64:
            raise RuntimeError("Empty screenshot data")
        out_path.write_bytes(base64.b64decode(b64))
        return body_text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--prefer-host", default="work.mercor.com")
    ap.add_argument("--tab-id", default="")
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument(
        "--click",
        action="append",
        default=[],
        help="Click the first <button> whose innerText includes this string (can be repeated; executed in order).",
    )
    ap.add_argument(
        "--wait-any",
        action="append",
        default=[],
        help="After clicks, wait until body text contains ANY of these strings (case-insensitive). Can be repeated.",
    )
    args = ap.parse_args()

    if args.tab_id:
        target = next(
            (t for t in _json_get(f"http://localhost:{args.port}/json/list") if t.get("id") == args.tab_id),
            None,
        )
        if not target:
            raise SystemExit(f"Tab id not found: {args.tab_id}")
    else:
        target = _pick_page_target(args.port, args.prefer_host)
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        raise SystemExit("Selected target missing webSocketDebuggerUrl")

    out_path = Path(args.out).expanduser().resolve()
    body_text = asyncio.run(
        _cdp_capture(
            ws_url,
            args.url,
            out_path,
            timeout_s=args.timeout,
            click_texts=args.click,
            wait_any=args.wait_any,
        )
    )

    # Print minimal status hints (stdout only).
    text_l = body_text.lower()
    if "your application has been submitted" in text_l:
        print("STATUS=SUBMITTED")
    elif "application submitted" in text_l:
        print("STATUS=SUBMITTED_MAYBE")
    elif "continue application" in text_l:
        print("STATUS=IN_PROGRESS")
    elif "sign in" in text_l and "mercor" in text_l:
        print("STATUS=NOT_LOGGED_IN")
    else:
        print("STATUS=UNKNOWN")

    print(f"SCREENSHOT={out_path}")


if __name__ == "__main__":
    main()
