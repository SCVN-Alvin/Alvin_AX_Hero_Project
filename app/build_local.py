#!/usr/bin/env python3
"""Create a portable local HTML + Python server bundle without Node or npm."""
import base64
import json
from pathlib import Path
import re
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
OUT = ROOT / "local-build"


def script(code):
    return "<script>\n" + re.sub(r"</script", r"<\\/script", code, flags=re.I) + "\n</script>"


def data_url(path, mime):
    return "data:" + mime + ";base64," + base64.b64encode(path.read_bytes()).decode()


def main():
    OUT.mkdir(exist_ok=True)
    # Font URLs contain semicolons, so remove the entire import line.
    css = re.sub(r"(?m)^@import[^\n]*\n?", "", (DIST / "styles.css").read_text())
    css += "\n" + (DIST / "base_projects.css").read_text() + "\n" + (ROOT / "local_ai.css").read_text()
    # The local server's content policy allows data: fonts only, which also keeps index.html self-contained.
    font = data_url(DIST / "assets" / "PretendardVariable.woff2", "font/woff2")
    css = css.replace("url(assets/PretendardVariable.woff2)", "url(" + font + ")")
    sources = (DIST / "sources.json").read_text()
    # JSON is placed inside a script tag; escape '<' to prevent closing the tag.
    sources_js = json.dumps(json.loads(sources), ensure_ascii=False).replace("<", "\\u003c")
    assets = {"assets/" + p.name: data_url(p, "image/png") for p in (DIST / "assets").glob("*.png")}
    bootstrap = "const EMBEDDED_SOURCES=" + sources_js + ";\nconst LOCAL_ASSETS=" + json.dumps(assets) + ";"
    app = (DIST / "app.js").read_text()
    fetch_source = "const r=await fetch('sources.json');if(!r.ok)throw Error('Source library could not load.');state.sources=await r.json();"
    if fetch_source not in app:
        raise SystemExit("Source-loading code changed. Update build_local.py before rebuilding.")
    app = app.replace(fetch_source, "state.sources=clone(EMBEDDED_SOURCES);")
    app = app.replace('src="${esc(im.url)}"', 'src="${esc(LOCAL_ASSETS[im.url]||im.url)}"')
    app = app.replace('href="source-brief.txt"', 'href="' + data_url(DIST / "source-brief.txt", "text/plain;charset=utf-8") + '"')
    app = re.sub(r"\ninit\(\);\s*$", "\n", app)
    app += "\n" + (DIST / "base_projects.js").read_text() + "\n" + (ROOT / "local_ai.js").read_text() + "\ninit();\n"
    html = (DIST / "index.html").read_text()
    html = html.replace('<title>Briefboard · Crusher Factory</title>', '<title>Briefboard Local · OpenAI Planner</title>')
    html = html.replace('<script src="base_projects.js"></script>', '').replace('<link rel="stylesheet" href="base_projects.css">', '')
    html = html.replace('<link rel="stylesheet" href="styles.css">', '<style>\n' + css + '\n</style>')
    html = html.replace('<script src="seed.js"></script>', script(bootstrap) + '\n' + script((DIST / "seed.js").read_text()))
    html = html.replace('<script src="app.js"></script>', script(app))
    (OUT / "index.html").write_text(html)
    shutil.copy2(ROOT / "local_server.py", OUT / "server.py")
    (OUT / "START_LOCAL.command").write_text('#!/bin/zsh\ncd -- "${0:A:h}"\nexec python3 server.py --directory .\n')
    (OUT / "START_LOCAL.command").chmod(0o755)
    (OUT / "README.txt").write_text("""BRIEFBOARD LOCAL + OPENAI

Requirements: Python 3.10+; no Python packages, Node or npm.
The board, styles, source text, maps and JavaScript are embedded in index.html.
Only AI generation needs internet access, plus one of:
  - the Codex CLI, signed in with your own ChatGPT account (codex login), or
  - an OpenAI API key.

START ON MAC / LINUX
1. Open Terminal in this folder and run: python3 server.py --directory .
   The server reports whether Codex is installed and signed in.
   To use an API key instead, see ALTERNATIVE START.
2. Open http://127.0.0.1:8786 in your browser.
3. Click Import brief. Upload HTML/Markdown/text or paste slide text.
4. Choose an import mode and click Generate with AI. Review the task hierarchy,
   disciplines, criteria, source evidence and questions before adding tasks.
   Base project ranks source-backed requirements; aliases are merged and
   repeated mentions do not add credit. Choose a base manually if needed.
5. Ctrl+C in Terminal stops the server.

CHATGPT (CODEX) MODE
Briefboard checks that Codex is installed, supported and signed in, then runs it
on this computer. It never asks for your ChatGPT password and never reads Codex's
credential files. Usage counts against your own ChatGPT plan. Leave Model empty to
use Codex's default; ChatGPT plans do not offer every API model. Codex runs in
read-only mode from an empty temporary folder with tools turned off. That reduces
risk but is not full isolation, so only import briefs you trust. Set CODEX_BINARY
if Codex is installed somewhere unusual.

ALTERNATIVE START (API KEY)
Set the API key without displaying it or putting it in command history:
   bash -c 'read -rsp "OpenAI API key: " OPENAI_API_KEY; echo; export OPENAI_API_KEY; exec python3 server.py --directory .'
On macOS, START_LOCAL.command launches the board too. A double-clicked launcher
may not inherit terminal environment variables, so use the terminal command for
API keys. Codex mode works from either launcher.
Without Codex or an API key the board and local-rule extraction still work.
On Windows PowerShell, set OPENAI_API_KEY in the process environment using your
preferred secure secret mechanism, then run: python server.py --directory .

MODEL (API KEY MODE)
Default: gpt-6-astra, following the official API quickstart at build time.
The model ID is editable in the import dialog. Your API project must have access
and the model must support Responses API Structured Outputs.
You can also export OPENAI_MODEL before starting the server.
If port 8786 is in use: python3 server.py --directory . --port 8787

HOW IT WORKS
Browser HTML -> same-origin local Python server -> your Codex CLI or the OpenAI
Responses API. The API key stays in the server environment and is never passed
to Codex. It is never embedded in HTML, browser storage, workspace exports or
server logs. The server listens only on 127.0.0.1. AI generation submits the
imported text and source name only when requested, with no automatic retries.
API-key usage bills the configured API project.
The Pretendard font is embedded in index.html, so no font requests are made.
Source HTML is converted to text. Linked images, videos and Google Slides
are not retrieved or analyzed. This demo supports video tags 001 through 008.

PERSISTENCE AND TRANSFER
Data stays in this browser, under this localhost origin and port. It is not
shared with other browsers or devices. Use Export workspace for backups.
To transfer edits made in the hosted demo, export its workspace JSON, then
Import brief -> choose that JSON -> Replace & restore in this local version.
Keep the port consistent so the browser uses the same saved workspace.

STATIC HOSTING
index.html is self-contained and can be copied to a static host. The board and
local-rule imports work without this server, but AI generation requires the
same-origin /api routes implemented in server.py.
Do not place an API key inside HTML or JavaScript.

VERIFICATION
Python and JavaScript checks plus mocked API/HTTP integration tests are supplied
in the source checkout. Live generation requires your own Codex sign-in or API key.
""")
    archive = ROOT / "briefboard-local-openai.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for filename in ("index.html", "server.py", "START_LOCAL.command", "README.txt"):
            z.write(OUT / filename, "briefboard-local/" + filename)
    print(f"Local build: {OUT}")
    print(f"Portable ZIP: {archive} ({archive.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
