"""Builds a small Notion-style export (HTML + companion folder) for import tests."""

import base64
import os
import urllib.parse

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 64 + b"\xff\xd9"
# Not a decodable video, but a valid-looking ISO media header; enough for type sniffing and byte ranges.
MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(range(256)) * 40
HASH = "3e307693313580bdbd14dda52d06dd01"


def enc(path):
    return "/".join(urllib.parse.quote(p, safe="()") for p in path.split("/"))


def default_html(folder, *, missing=("gone.png",), extra_body=""):
    f = enc(folder)
    figs = f"""
<figure id="f1" class="image" data-notion-image="{f}/shot%20one.png"><a href="{f}/shot%20one.png"><img style="width:432px" src="{f}/shot%20one.png"/></a></figure>
<figure id="f2" dir="ltr"><div class="source"><a href="{f}/clip.mp4">attachment:7255cc48-2c6d-481f-94a1-e3a3954481b3:clip.mp4</a></div></figure>
<figure id="f3" class="image"><img src="{f}/anim.gif"/></figure>
<figure id="f4" class="image"><img src="{f}/%C4%91%C6%B0%E1%BB%9Dng.jpg"/></figure>"""
    for m in missing:
        figs += f'\n<figure class="image"><img src="{f}/{enc(m)}"/></figure>'
    return f"""<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"/><title>Test Brief</title>
<style>@import url('https://cdn.example.test/katex.css');</style><script>alert(1)</script></head><body>
<article id="page1" class="page sans"><header><h1 class="page-title">Test Brief</h1>
<table class="properties"><tbody><tr class="property-row"><th>Assignee</th><td><span class="user">Zoe</span></td></tr>
<tr class="property-row"><th>Art %</th><td>60</td></tr></tbody></table></header>
<div class="page-body">
<details open=""><summary style="font-weight:600"><h3 id="h-old" style="display:inline-block">1st (01/07)</h3></summary><div class="indented">
<ul class="bulleted-list"><li style="list-style-type:disc">Old idea: use a rope.</li></ul></div></details>
<h2 id="h-goal">1. Goal</h2>
<ul class="bulleted-list"><li>Make the log game. <strong>Important</strong> and <a href="https://example.test/ref">a link</a></li></ul>
<h2 id="h-scen">2. Scenario</h2>
<table id="t1" class="simple-table"><thead class="simple-table-header"><tr id="r0"><th><strong>Video No.</strong></th><th><strong>Concept</strong></th><th><strong>Scenario</strong></th></tr></thead><tbody>
<tr id="r1"><td>001</td><td>Right log → fence</td><td>⚔️<strong> [Default theme]<br/></strong><br/> 1. Hammer right log<br/> 2. Shave wood<br/> 3. Stack wood<br/> 3. Put down fence<br/> 4. Done</td></tr>
<tr id="r2"><td>002 </td><td>Wrong log</td><td>1. Hammer wrong log<br/>2. Shave bad wood<br/>stray line<br/>3. Stack</td></tr>
<tr id="r3"><td>003</td><td>AI hook</td><td>Video 001</td></tr></tbody></table>
<h2 id="h-det">3. Details</h2>
<h4 id="h-ship">3.1. Wood shave:</h4>
<ul class="bulleted-list"><li>Handheld wood shaver<ul class="bulleted-list"><li style="list-style-type:circle">By hand: 10 wood logs</li></ul><ul class="bulleted-list"><li>By saw machine: 40 wood logs</li></ul></li></ul>
<p id="p-neg">No build fence animation</p>
<p>Tiếng gấu/skeleton 1 lần mỗi wave &amp; Đường dây</p>
{figs}
{extra_body}
</div></article></body></html>"""


def make_export(root, name="Test Brief", *, html=None, files=None, missing=("gone.png",), with_hash=True):
    """Return (html_path, folder_path). `files` maps companion-relative names to bytes."""
    os.makedirs(root, exist_ok=True)
    stem = f"{name} {HASH}" if with_hash else name
    folder = os.path.join(root, name)
    os.makedirs(folder, exist_ok=True)
    if files is None:
        files = {"shot one.png": PNG, "clip.mp4": MP4, "anim.gif": GIF, "đường.jpg": JPG}
    for rel, data in files.items():
        path = os.path.join(folder, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    html_path = os.path.join(root, stem + ".html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html if html is not None else default_html(name, missing=missing))
    return html_path, folder
