"""Record real Chromium accessibility trees of the target app as test fixtures.

Perception is pure - AX nodes in, RawElements out - so it is unit-tested against
recorded trees rather than a live browser. Re-run after changing any template
under target_app/:

    uv run python scripts/capture_ax_fixtures.py

Each fixture holds, per frame, the CDP frame id, its frame path and the raw
``Accessibility.getFullAXTree`` nodes, plus DOM attributes (type, autocomplete)
for form controls, which the accessibility tree does not carry. The target app's
data is fictional, so recording it verbatim is safe.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import Frame, Page, sync_playwright

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "tests" / "fixtures" / "ax"
FORM_TAGS = {"INPUT", "SELECT", "TEXTAREA"}
KEPT_ATTRS = ("type", "autocomplete", "name", "id")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_app(port: int, log: Path) -> subprocess.Popen[bytes]:
    proc = subprocess.Popen(
        [sys.executable, "-m", "target_app"],
        cwd=REPO,
        env={**os.environ, "PORT": str(port)},
        stdout=log.open("wb"),
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5):
                return proc
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError(f"target app not ready; see {log}")


def _attrs(node: dict[str, Any]) -> dict[str, str]:
    a = node.get("attributes", [])
    return dict(zip(a[::2], a[1::2], strict=False))


def index_dom(root: dict[str, Any]) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]:
    """Frame id -> frame path, and backendNodeId -> attributes for form controls."""
    frame_paths: dict[str, list[str]] = {}
    form_attrs: dict[str, dict[str, str]] = {}

    def walk(node: dict[str, Any], path: list[str]) -> None:
        if node.get("nodeName") in FORM_TAGS:
            a = _attrs(node)
            form_attrs[str(node["backendNodeId"])] = {k: a[k] for k in KEPT_ATTRS if k in a}
        for child in node.get("children", []):
            walk(child, path)
        if "contentDocument" in node:
            a = _attrs(node)
            fallback = f"iframe#{a['id']}" if a.get("id") else node["nodeName"].lower()
            seg = a.get("name") or fallback
            sub = [*path, seg]
            if node.get("frameId"):
                frame_paths[node["frameId"]] = sub
            walk(node["contentDocument"], sub)

    walk(root, ["main"])
    return frame_paths, form_attrs


def capture(cdp: Any) -> dict[str, Any]:
    tree = cdp.send("Page.getFrameTree")["frameTree"]

    def flatten(ft: dict[str, Any]) -> list[dict[str, Any]]:
        return [ft["frame"], *[f for c in ft.get("childFrames", []) for f in flatten(c)]]

    frames = flatten(tree)
    dom = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})["root"]
    frame_paths, form_attrs = index_dom(dom)
    frame_paths[tree["frame"]["id"]] = ["main"]
    return {
        "frames": [
            {
                "frame_id": f["id"],
                "url": f["url"],
                "path": frame_paths.get(f["id"], ["?"]),
                "nodes": cdp.send("Accessibility.getFullAXTree", {"frameId": f["id"]})["nodes"],
            }
            for f in frames
        ],
        "form_attrs": form_attrs,
    }


def save(name: str, data: dict[str, Any]) -> None:
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    nodes = sum(len(f["nodes"]) for f in data["frames"])
    print(f"  {path.relative_to(REPO)}: {len(data['frames'])} frames, {nodes} AX nodes")


def wait_for_frame(page: Page, match: Callable[[Frame], bool], what: str) -> Frame:
    """Poll until a matching frame exists.

    A frame only appears after its parent document has parsed, and ``load`` on a
    frame can resolve against the document being replaced - both bit the A3 spike.
    """
    for _ in range(200):
        frame = next((f for f in page.frames if match(f)), None)
        if frame is not None:
            return frame
        page.wait_for_timeout(50)
    raise RuntimeError(f"{what} never appeared")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    port = free_port()
    app = start_app(port, OUT / "capture_app.log")
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            cdp = page.context.new_cdp_session(page)
            cdp.send("DOM.enable")
            cdp.send("Accessibility.enable")

            # Navigation here is setup, driven by CSS ids; the recorded trees are the product.
            page.goto(f"{base}/")
            page.wait_for_selector("#ctl00_txtPassword")
            save("login", capture(cdp))

            page.fill("#ctl00_txtUserId", "operator1")
            page.fill("#ctl00_txtPassword", "changeme")
            page.click("#ctl00_btnSignOn")
            page.wait_for_url("**/console")
            content = wait_for_frame(page, lambda f: f.name == "content", "content frame")
            content.wait_for_selector("#ctl00_MainContent_txtMemberId")
            save("search", capture(cdp))

            content.fill("#ctl00_MainContent_txtMemberId", "12345")
            content.click("#ctl00_MainContent_btnSearch")
            content.wait_for_selector("#ctl00_MainContent_gvMembers")
            save("results", capture(cdp))

            content.click("a[href='/console/member?member_id=12345']")
            content.wait_for_selector("text=Member Profile")
            save("detail", capture(cdp))

            content.click("td:text-is('Accounts')")
            content.wait_for_selector("#ctl00_MainContent_ifrAccounts")
            accounts = wait_for_frame(
                page, lambda f: "/console/member/accounts" in f.url, "accounts iframe"
            )
            accounts.wait_for_selector("#ctl00_MainContent_gvAccounts")
            save("accounts", capture(cdp))
            browser.close()
    finally:
        app.terminate()
        app.wait(timeout=5)
        (OUT / "capture_app.log").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
