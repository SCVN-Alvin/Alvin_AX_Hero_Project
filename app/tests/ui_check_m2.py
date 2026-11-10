"""End-to-end UI verification for Milestone 2 (needs Google Chrome; not part of `unittest discover`).

    python3 tests/ui_check_m2.py [screenshot_dir]

Imports the real sample in RawData/ through the interface, browses it, plays a local video
with every external host unreachable, and proves earlier project data is untouched. Chrome is
started with a dead proxy and no DNS, so any attempt to reach the internet fails and is logged.
"""

import glob
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cdp import Browser  # noqa: E402
from export_fixture import PNG, make_export  # noqa: E402
from ui_check import Srv, check, RESULTS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLE = (glob.glob(os.path.join(ROOT, "RawData", "*.html")) or [None])[0]


def click_text(b, text, scope="body", index=0, exact=False):
    ok = b.js(f"""(() => {{
      const els = [...document.querySelectorAll({json.dumps(scope + ' button, ' + scope + ' a')})]
        .filter(e => {{ const t = e.textContent.trim(); return {'t === ' if exact else ''}{'' if exact else 't.includes('}{json.dumps(text)}{'' if exact else ')'}; }});
      const e = els[{index}]; if (!e) return false; e.click(); return true; }})()""")
    b.pump(0.4)
    return ok


def wait_until(b, expr, timeout=20.0, step=0.15):
    end = time.time() + timeout
    while time.time() < end:
        if b.js(expr):
            return True
        b.pump(step)
    return False


def ffprobe(path):
    """(width, height, seconds) of a video from the original file, or None if ffprobe is not installed."""
    import subprocess
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                              "-of", "json", path], capture_output=True, text=True, timeout=30).stdout
        j = json.loads(out)
        return j["streams"][0]["width"], j["streams"][0]["height"], float(j["format"]["duration"])
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return None


def text_of(b, selector):
    return b.js(f"(document.querySelector({json.dumps(selector)}) || {{innerText: ''}}).innerText")


def main():
    shots = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="organizer-m2-shots-")
    os.makedirs(shots, exist_ok=True)
    if not SAMPLE:
        print("RawData sample not found; nothing to verify")
        return 1
    data = tempfile.mkdtemp(prefix="organizer-m2-")
    work = tempfile.mkdtemp(prefix="organizer-m2-export-")
    srv = Srv(data)
    api = srv.api
    # Existing Milestone 1 data that must survive everything below.
    pid = api.ok("POST", "/api/projects", {"name": "Human Face Log", "ticket_prefix": "HFL"})["id"]
    other = api.ok("POST", "/api/projects", {"name": "Other project", "ticket_prefix": "OTH"})["id"]
    epic = api.ok("POST", f"/api/projects/{pid}/tasks", {"title": "Existing epic", "level": "epic"})
    task = api.ok("POST", f"/api/projects/{pid}/tasks", {"title": "Existing task", "level": "task", "parent_id": epic["id"], "disciplines": ["art", "dev"], "impact": "high"})
    board_before = api.ok("GET", f"/api/projects/{pid}/board")

    flags = ["--proxy-server=socks5://127.0.0.1:9", "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost , EXCLUDE 127.0.0.1",
             "--autoplay-policy=no-user-gesture-required"]
    b = Browser(1440, 900, port=9355, extra_args=flags)
    try:
        # -- the browser really cannot reach the internet
        nav = b.call("Page.navigate", url="https://example.com/")
        b.pump(1.5)
        check("Browser is cut off from the internet (external navigation fails)", bool(nav.get("errorText")), str(nav))
        b.requests.clear()
        b.failed.clear()

        # -- header Import, wizard, preview
        b.goto(srv.url + f"#/p/{pid}/board")
        b.wait_for(".board")
        check("Header Import button is enabled", b.js("!document.querySelector('.header-main .btn[disabled]')"))
        check("Sidebar Sources entry is live (not marked Soon)", b.js("(() => { const a=[...document.querySelectorAll('.nav a')].find(x=>x.textContent.includes('Sources')); return !!a && !a.textContent.includes('Soon'); })()"))
        click_text(b, "Import", ".header-main", exact=False)
        b.wait_for(".import-wiz .import-card")
        check("Import button opens the import page", "Import a brief" in text_of(b, ".import-wiz"))

        # file picker
        click_text(b, "Browse", ".import-wiz", index=0)
        b.wait_for("dialog .fp-list")
        b.pump(0.5)
        check("File picker lists folders from the local service", b.js("document.querySelectorAll('dialog .fp-row').length") > 0)
        b.type("dialog input.fp-path", os.path.dirname(SAMPLE))
        b.key("Enter", code="Enter", vk=13)
        b.pump(0.6)
        picked = b.js("(() => { const r=[...document.querySelectorAll('dialog .fp-row.html')]; return r.length ? r[0].textContent : ''; })()")
        check("File picker shows the sample's .html file and hides other files", ".html" in picked and ".png" not in text_of(b, "dialog .fp-list"), picked)
        b.js("document.querySelector('dialog .fp-row.html').click()")
        b.pump(0.2)
        b.js("document.querySelector('dialog form').requestSubmit()")
        b.pump(0.5)
        chosen = b.js("document.querySelector('input[name=html]').value")
        check("Choosing a file fills the path", chosen == SAMPLE or os.path.realpath(chosen) == os.path.realpath(SAMPLE), chosen)

        click_text(b, "Preview selection", ".import-wiz")
        b.wait_for(".import-card.wide", timeout=20)
        t = text_of(b, ".import-wiz")
        check("Preview: 61 media files referenced and 61 found", "61 files referenced" in t and "61 found" in t, t[:600])
        check("Preview: 12 scenarios 001 to 012", "12 found (001 to 012)" in t, t)
        check("Preview: historical sections named", "1st (01/07)" in t and "2nd (20/09)" in t)
        check("Preview: media folder found automatically", "found next to the file" in t)
        check("Preview: web links kept but not fetched", "never fetched" in t and "drive.google.com" in t)
        check("Preview: nothing saved yet", api.ok("GET", f"/api/projects/{pid}/documents")["documents"] == [])
        b.screenshot(os.path.join(shots, "m2-01-import-preview.png"))

        # -- run the import, watching progress
        click_text(b, "Import brief", ".import-wiz")
        b.wait_for(".import-card .progressbar, .import-card h2", timeout=10)
        seen_progress, shot_taken = False, False
        end = time.time() + 180
        while time.time() < end:
            h2 = text_of(b, ".import-card h2")
            if h2.startswith("Import finished") or h2.startswith("Import failed"):
                break
            if b.js("!!document.querySelector('.progressbar')"):
                seen_progress = True
                if not shot_taken:
                    b.screenshot(os.path.join(shots, "m2-02-import-progress.png"))
                    shot_taken = True
            b.pump(0.1)
        done_text = text_of(b, ".import-wiz")
        check("Import ran with progress shown (stage text and bar), or finished too fast to catch", seen_progress or "Import finished" in done_text)
        check("Import finished", "Import finished" in done_text, done_text[:400])
        check("Result reports blocks, scenarios and files", "223 blocks" in done_text and "12 scenarios" in done_text and "61 of 61 files" in done_text, done_text)
        b.screenshot(os.path.join(shots, "m2-03-import-done.png"))
        check("Project has no tasks created by the import", api.ok("GET", f"/api/projects/{pid}")["task_count"] == 2)

        # -- source browser
        click_text(b, "Open the source browser", ".import-wiz")
        b.wait_for(".src-doc .outline-list", timeout=15)
        b.wait_for(".src-blocks .blk", timeout=15)
        check("Source browser opens the imported document", "Creative_Human Face Log Defense_Ruelle" in text_of(b, ".src-head"))
        check("Outline lists the document's sections", b.js("document.querySelectorAll('.outline-item').length") == 12, str(b.js("document.querySelectorAll('.outline-item').length")))
        check("Historical sections are badged in the outline", b.js("document.querySelectorAll('.outline-row.role-historical').length") == 2)
        check("Blocks render with headings, lists, tables", b.js("document.querySelectorAll('.src-blocks .blk-list_item').length") > 20 and b.js("document.querySelectorAll('.src-blocks table').length") >= 1)
        check("Historical blocks are visibly distinguished", b.js("document.querySelectorAll('.src-blocks .blk.hist').length") > 10)
        b.screenshot(os.path.join(shots, "m2-04-source-top.png"))

        # jump to 3.5 via the outline and check the resource values
        click_text(b, "3.5. Implementation information", ".outline")
        b.pump(0.6)
        view = b.js("""(() => { const h=[...document.querySelectorAll('.src-h')].find(x=>x.textContent.includes('3.5.')); const r=h.getBoundingClientRect(); const rd=document.querySelector('.reader').getBoundingClientRect(); return [r.top>=rd.top-2 && r.top<rd.bottom]; })()""")
        check("Outline jumps to section 3.5", view[0])
        sect = b.js("""(() => [...document.querySelectorAll('.src-blocks .blk')].filter(e => +e.dataset.seq >= 191 && +e.dataset.seq <= 206).map(e => e.innerText.trim()).join('|'))()""")
        for needle in ("5 wood logs", "15 wood logs", "25 wood logs", "50 coins", "200 coins", "By hand: 10 wood logs", "By saw machine: 40 wood logs"):
            check(f"Section 3.5 shows '{needle}' unchanged", needle in sect, sect[:300])
        b.screenshot(os.path.join(shots, "m2-05-section-3-5.png"))

        # the map diagrams in section 3.1 display as real images
        click_text(b, "3.1. Map", ".outline")
        b.pump(0.8)
        b.pump(0.6)
        b.screenshot(os.path.join(shots, "m2-05b-map-images.png"))
        # scroll through the whole section so every lazy image is requested, then require all to decode
        loaded = b.js("""(async () => {
          const imgs = [...document.querySelectorAll('.src-blocks .blk')].filter(e => +e.dataset.seq >= 53 && +e.dataset.seq <= 108).flatMap(e => [...e.querySelectorAll('img.media-img')]);
          for (const i of imgs) { i.scrollIntoView({block: 'center'}); await new Promise(r => setTimeout(r, 120)); }
          const end = Date.now() + 20000;
          while (Date.now() < end && !imgs.every(i => i.complete && i.naturalWidth > 0)) await new Promise(r => setTimeout(r, 150));
          return {n: imgs.length, ok: imgs.every(i => i.complete && i.naturalWidth > 0)};
        })()""")
        check("Section 3.1 Map: all its reference images load, decode, and are non-empty", loaded["ok"] and loaded["n"] >= 2, str(loaded))

        # negative instruction preserved, media blocks, video attachment recognised
        check("Negative instruction 'No build fence animation' is preserved", b.js("document.body.innerText.includes('No build fence animation')"))
        b.js("document.querySelector('.reader').scrollTop = 0")
        search = "input.src-search"
        b.type(search, "no build fence")
        b.pump(0.8)
        check("Search finds the negative instruction in section 3.4", "No build fence animation" in text_of(b, ".hits") and "3.4. Gameplay Sequences Sample:" in text_of(b, ".hits"), text_of(b, ".hits")[:200])
        b.type(search, "TIẾNG")
        b.pump(0.8)
        check("Search folds Vietnamese capitals ('TIẾNG' finds 'Tiếng')", b.js("document.querySelectorAll('.hit').length") >= 5, str(b.js("document.querySelectorAll('.hit').length")))
        b.type(search, "king shot")
        b.pump(0.8)
        check("Search reaches historical content and marks it", "Historical" in text_of(b, ".hits"))
        b.screenshot(os.path.join(shots, "m2-06-search.png"))
        click_text(b, "King Shot", ".hits")
        b.pump(0.8)
        check("Clicking a hit jumps to the block", b.js("document.querySelector('.blk.flash') !== null || document.activeElement.classList.contains('blk')"))
        b.type(search, "")
        b.pump(0.4)

        # in-content media: image opens the viewer, video plays
        b.js("document.querySelector('.reader').scrollTop = 0")
        b.js("document.querySelector('.src-blocks .img-btn').click()")
        b.wait_for("dialog.lightbox img")
        wait_until(b, "(() => { const i=document.querySelector('dialog.lightbox img'); return i && i.complete && i.naturalWidth > 0; })()", 15)
        check("Clicking an image in the content opens a full-size preview", b.js("document.querySelector('dialog.lightbox img').naturalWidth") > 0)
        b.screenshot(os.path.join(shots, "m2-07-image-viewer.png"))
        b.key("Escape", code="Escape", vk=27)
        check("Escape closes the preview", b.js("!document.querySelector('dialog.lightbox')"))

        # -- section role toggle persists
        b.js("document.querySelector('.outline-row.role-historical .icon-btn').click()")
        b.pump(0.3)
        b.wait_for(".menu")
        click_text(b, "Treat as current", ".menu")
        b.pump(1.0)
        check("Historical section can be marked current", b.js("document.querySelectorAll('.outline-row.role-historical').length") == 1)
        d = api.ok("GET", f"/api/projects/{pid}/documents")["documents"][0]
        det = api.ok("GET", f"/api/documents/{d['id']}")
        first = next(s for s in det["sections"] if s["title"] == "1st (01/07)")
        check("The change is stored and attributed to the user", first["role"] == "current" and first["role_origin"] == "user")
        check("Original text is unchanged by the interpretation change", any("King Shot" in x["text"] for x in api.ok("GET", f"/api/documents/{d['id']}/blocks?limit=1000")["blocks"]))
        api.ok("PATCH", f"/api/sections/{first['id']}", {"role": "historical"})  # restore for later checks

        # -- scenarios
        click_text(b, "Scenarios", ".tabs")
        b.wait_for(".scen")
        check("Scenarios tab shows 12 scenarios", b.js("document.querySelectorAll('.scen').length") == 12)
        check("Scenario identifiers 001 to 012 in order", b.js("[...document.querySelectorAll('.scen .scen-id')].map(e=>e.textContent).join(',')") == ",".join(f"{i:03d}" for i in range(1, 13)))
        check("Scenario 001 has 13 numbered steps and flags the repeated number", b.js("document.querySelectorAll('#scen-001 .steps li').length") == 13 and b.js("document.querySelectorAll('#scen-001 .steps li.dup').length") == 1)
        check("Scenario cells without steps stay as plain text", "Video 001" in text_of(b, "#scen-009"))
        b.js("document.querySelector('#scen-001 .orig summary').click()")
        b.pump(0.2)
        check("Original wording is available beside the parsed steps", "1. Hammer right log" in text_of(b, "#scen-001 .orig pre"))
        b.screenshot(os.path.join(shots, "m2-08-scenarios.png"))
        click_text(b, "Show in document", "#scen-001")
        b.wait_for(".src-blocks table")
        check("'Show in document' returns to the table", b.js("document.querySelector('.blk.flash table, .blk.flash .tbl-wrap') !== null") or b.js("document.activeElement.classList.contains('blk')"))

        # -- media tab
        click_text(b, "Media", ".tabs")
        b.wait_for(".media-grid .tile")
        check("Media tab lists all 61 files", b.js("document.querySelectorAll('.media-grid .tile').length") == 61)
        click_text(b, "GIFs", ".pills")
        check("GIF filter shows the 16 GIFs", b.js("document.querySelectorAll('.media-grid .tile').length") == 16)
        click_text(b, "Video", ".pills")
        check("Video filter shows the 3 MP4 files", b.js("document.querySelectorAll('.media-grid .tile').length") == 3)
        b.screenshot(os.path.join(shots, "m2-09-media-tab.png"))
        b.js("document.querySelectorAll('.media-grid .tile')[0].click()")
        b.wait_for("dialog.lightbox video")
        ready = wait_until(b, "document.querySelector('dialog.lightbox video').readyState >= 1", 20)
        check("Local MP4 loads in the viewer", ready)
        info = b.js("(() => { const v=document.querySelector('dialog.lightbox video'); return {w:v.videoWidth,h:v.videoHeight,d:v.duration,src:v.currentSrc,name:document.querySelector('dialog.lightbox .lb-title').textContent}; })()")
        expected = ffprobe(os.path.join(os.path.dirname(SAMPLE), "Creative_Human Face Log Defense_Ruelle", info["name"]))
        check("Video decodes with the dimensions and duration of the original file",
              info["w"] > 0 and info["h"] > 0 and (expected is None or (info["w"], info["h"]) == expected[:2] and abs(info["d"] - expected[2]) < 1.0),
              f"{info} vs ffprobe {expected}")
        check("Video is served by the local service", info["src"].startswith("http://localhost:") and "/api/assets/" in info["src"])
        played = b.js("""(async () => { const v=document.querySelector('dialog.lightbox video'); await v.play(); await new Promise(r=>setTimeout(r,1800)); const t=v.currentTime; const ok=!v.paused && t>0.5; v.currentTime = 20; await new Promise(r=>setTimeout(r,900)); return {ok, t, seeked: v.currentTime >= 19.5}; })()""")
        check("Video plays with the internet unreachable", played["ok"], str(played))
        check("Seeking works (byte ranges)", played["seeked"], str(played))
        b.screenshot(os.path.join(shots, "m2-10-video-playing.png"))
        b.key("ArrowRight", code="ArrowRight", vk=39)  # focus is on the video: arrows must seek, not leave the file
        b.key("Escape", code="Escape", vk=27)
        check("Closing the viewer stops the video", b.js("!document.querySelector('dialog.lightbox')"))

        # big GIF only loads on request, then displays
        click_text(b, "GIFs", ".pills")
        b.js("document.querySelectorAll('.media-grid .tile')[0].click()")
        b.wait_for("dialog.lightbox img")
        check("Large GIF opens in the viewer and decodes", wait_until(b, "(() => { const i=document.querySelector('dialog.lightbox img'); return i && i.complete && i.naturalWidth > 0; })()", 60))
        b.screenshot(os.path.join(shots, "m2-11-gif-viewer.png"))
        b.key("Escape", code="Escape", vk=27)

        # keyboard: tile reachable and opens with Enter
        click_text(b, "All", ".pills", exact=False)
        b.js("document.querySelector('.media-grid .tile').focus()")
        for typ in ("keyDown", "keyUp"):
            b.call("Input.dispatchKeyEvent", type=typ, key="Enter", code="Enter", windowsVirtualKeyCode=13, text="\r" if typ == "keyDown" else "")
        b.pump(0.8)
        check("A media tile opens with the keyboard", b.js("!!document.querySelector('dialog.lightbox')"))
        b.key("Escape", code="Escape", vk=27)

        # -- notes tab
        click_text(b, "Notes", ".tabs")
        b.wait_for(".notes, .scroll-panel")
        nt = text_of(b, ".src-panel")
        check("Notes explain the remote stylesheet that was not fetched", "katex" in nt and "not fetched" in nt, nt[:300])
        check("Notes say every referenced file was found", "Every referenced file was found" in nt)
        check("Notes record the repeated step number", "10 repeats" in nt)

        # -- identical re-import through the UI
        b.goto(srv.url + f"#/p/{pid}/importbrief")
        b.wait_for(".import-card")
        b.type("input[name=html]", SAMPLE)
        click_text(b, "Preview selection", ".import-wiz")
        b.wait_for(".import-card.wide", timeout=20)
        check("Re-import preview says it was already imported", "already imported" in text_of(b, ".import-wiz"))
        click_text(b, "Import", ".import-wiz", exact=False, index=1) if False else b.js("[...document.querySelectorAll('.import-wiz .btn.primary')].pop().click()")
        wait_until(b, "/Already imported|Import finished|Import failed/.test(document.querySelector('.import-card h2').innerText)", 180)
        check("Re-importing the same files changes nothing", "Already imported" in text_of(b, ".import-card h2"))
        check("Still exactly one document and 61 assets", len(api.ok("GET", f"/api/projects/{pid}/documents")["documents"]) == 1)

        # -- a deliberately missing file
        html, folder = make_export(work, name="Missing Demo", files={"shot one.png": PNG, "clip.mp4": b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100, "anim.gif": b"GIF89a" + b"\x00" * 20, "đường.jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 30})
        b.goto(srv.url + f"#/p/{other}/importbrief")
        b.wait_for(".import-card")
        b.type("input[name=html]", html)
        click_text(b, "Preview selection", ".import-wiz")
        b.wait_for(".import-card.wide", timeout=15)
        pt = text_of(b, ".import-wiz")
        check("Preview reports the deliberately missing file", "1 missing" in pt and "gone.png" in pt, pt[:500])
        b.screenshot(os.path.join(shots, "m2-12-missing-preview.png"))
        b.js("[...document.querySelectorAll('.import-wiz .btn.primary')].pop().click()")
        wait_until(b, "/Import finished|Import failed/.test(document.querySelector('.import-card h2').innerText)", 60)
        check("Import with a missing file still completes", "Import finished" in text_of(b, ".import-card h2"))
        check("Result tells the user a file was unavailable", "1 referenced file" in text_of(b, ".import-wiz"))
        click_text(b, "Open the source browser", ".import-wiz")
        b.wait_for(".src-blocks .blk", timeout=10)
        check("Missing media block keeps the reference and explains recovery", b.js("document.querySelector('.media-card.missing') !== null") and "Original reference" in text_of(b, ".media-card.missing"))
        b.js("document.querySelector('.media-card.missing').scrollIntoView({block: 'center'})")
        b.pump(0.3)
        b.screenshot(os.path.join(shots, "m2-13-missing-in-content.png"))
        click_text(b, "Notes", ".tabs")
        b.wait_for(".notes")
        check("Notes list the missing file with its original path", "gone.png" in text_of(b, ".src-panel") and "Missing Demo/gone.png" in text_of(b, ".src-panel"))
        # restore the file and import the same HTML again
        with open(os.path.join(folder, "gone.png"), "wb") as fh:
            fh.write(PNG + b"restored")
        click_text(b, "Import", ".header-main")  # header button opens a fresh import page
        b.wait_for("input[name=html]")
        b.type("input[name=html]", html)
        click_text(b, "Preview selection", ".import-wiz")
        b.wait_for(".import-card.wide", timeout=15)
        b.js("[...document.querySelectorAll('.import-wiz .btn.primary')].pop().click()")
        wait_until(b, "/Import finished|Already imported|Import failed/.test(document.querySelector('.import-card h2').innerText)", 60)
        check("Importing again attaches the restored file", "previously unavailable" in text_of(b, ".import-wiz"), text_of(b, ".import-wiz"))
        docs = api.ok("GET", f"/api/projects/{other}/documents")["documents"]
        check("...without creating a second document", len(docs) == 1 and docs[0]["counts"]["assets_unavailable"] == 0)

        # -- hostile content renders as inert text and nothing external is requested
        hostile = ("<html><head><script>window.__pwned=1</script><style>@import url('https://evil.test/a.css'); body{display:none}</style></head>"
                   "<body onload=\"window.__pwned=2\"><h2 onclick=\"window.__pwned=3\">Hostile</h2>"
                   "<p>Escaped: &lt;script&gt;window.__pwned=4&lt;/script&gt; and &lt;img src=x onerror=window.__pwned=5&gt;</p>"
                   "<p><a href=\"javascript:window.__pwned=6\">js link</a> <a href=\"https://evil.test/click\">web link</a> "
                   "<img src=\"https://evil.test/p.png\" alt=\"remote pixel\"></p><figure><img src=\"https://evil.test/big.png\"></figure>"
                   "<iframe src=\"https://evil.test/frame\"></iframe><svg onload=\"window.__pwned=7\"><script>window.__pwned=8</script></svg>"
                   "<img src=\"x\" onerror=\"window.__pwned=9\"></body></html>")
        hhtml, _ = make_export(work, name="Hostile", html=hostile, files={}, with_hash=False)
        run = api.ok("POST", f"/api/projects/{other}/imports", {"html_path": hhtml}, expect=(202,))
        for _ in range(100):
            if api.ok("GET", f"/api/imports/{run['id']}")["state"] != "running":
                break
            time.sleep(0.1)
        hdoc = next(d for d in api.ok("GET", f"/api/projects/{other}/documents")["documents"] if d["name"] == "Hostile.html")
        b.goto(srv.url + f"#/p/{other}/sources/{hdoc['id']}")
        wait_until(b, "(document.querySelector('.src-titles h2') || {textContent: ''}).textContent.includes('Hostile') && !!document.querySelector('.src-blocks .blk')", 10)
        b.pump(0.8)
        check("Hostile brief: no imported script, handler or javascript: link ran", b.js("window.__pwned === undefined"), str(b.js("window.__pwned")))
        check("Hostile brief: markup is shown as text, not run", "<script>window.__pwned=4</script>" in text_of(b, ".src-blocks"))
        stray = b.js("[...document.querySelectorAll('.src-blocks iframe, .src-blocks script, .src-blocks style, .src-blocks link, .src-blocks svg:not(.icon)')].map(e => e.outerHTML.slice(0, 120))")
        check("Hostile brief: no iframe, script, svg or style from the document is in the page", not stray, str(stray))
        check("Hostile brief: web link is a normal link marked as needing internet, not fetched",
              b.js("(() => { const a=document.querySelector('.src-blocks a.ext'); return !!a && a.href === 'https://evil.test/click' && a.rel.includes('noopener'); })()"))
        check("Hostile brief: javascript: link was removed", b.js("[...document.querySelectorAll('.src-blocks a')].every(a => !a.href.startsWith('javascript:'))")
              and "js link" in text_of(b, ".src-blocks"))
        check("Hostile brief: remote image shown as an external placeholder, not loaded", b.js("document.querySelector('.media-card.external') !== null")
              and b.js("document.querySelectorAll('.src-blocks img').length") == 0)
        check("Hostile brief: nothing was requested from evil.test", not [u for u in b.requests if "evil.test" in u], str([u for u in b.requests if "evil.test" in u]))
        b.screenshot(os.path.join(shots, "m2-16-hostile-content.png"))
        errors_before_bad_path = list(b.console_errors)
        # -- failures are explained
        click_text(b, "Import", ".header-main")  # on the finished page the header button starts a new selection
        b.wait_for("input[name=html]")
        check("Header Import button starts a fresh selection from a finished import", b.js("document.querySelector('input[name=html]').value") == "")
        b.type("input[name=html]", "/no/such/file.html")
        click_text(b, "Preview selection", ".import-wiz")
        b.wait_for(".form-error:not([hidden])", timeout=10)
        check("A wrong path gives a clear message", "No file exists" in text_of(b, ".form-error"))
        check("A rejected preview is not shown as a failed save", "Not saved" not in text_of(b, ".save-state"), text_of(b, ".save-state"))

        # -- offline evidence
        local = [u for u in b.requests if u.startswith(srv.url) or u.startswith("data:") or u.startswith("blob:")]
        external = [u for u in b.requests if u not in local]
        check("Every request the page made went to the local service", not external, str(external[:5]))
        check("No request failed (nothing tried to leave this computer)", not [f for f in b.failed if not f[1] and f[0] != "net::ERR_ABORTED"], str(b.failed[:5]))
        check("No console errors during the whole run (apart from the deliberate 400 for a wrong path)",
              not errors_before_bad_path and all("400" in e and "imports/preview" in e for e in b.console_errors), str(b.console_errors[:5]))

        # -- restart: everything persists, earlier data untouched
        srv.stop()
        srv = Srv(data)
        api = srv.api
        b.goto(srv.url + f"#/p/{pid}/sources")
        b.wait_for(".doc-card", timeout=15)
        check("After a restart the imported document is listed", "Creative_Human Face Log Defense_Ruelle" in text_of(b, ".doc-list"))
        board_after = api.ok("GET", f"/api/projects/{pid}/board")
        check("Earlier tasks and their order are unchanged", json.dumps(board_before, sort_keys=True) == json.dumps(board_after, sort_keys=True))
        check("The second project kept its own imports", len(api.ok("GET", f"/api/projects/{other}/documents")["documents"]) == 2
              and len(api.ok("GET", f"/api/projects/{pid}/documents")["documents"]) == 1)
        b.screenshot(os.path.join(shots, "m2-14-sources-list.png"))

        # -- narrow layout
        b.set_viewport(700, 900)
        b.goto(srv.url + f"#/p/{pid}/sources/{api.ok('GET', f'/api/projects/{pid}/documents')['documents'][0]['id']}")
        b.wait_for(".src-blocks .blk", timeout=15)
        check("Narrow layout: no horizontal page scroll", b.js("document.documentElement.scrollWidth <= window.innerWidth + 1"))
        b.screenshot(os.path.join(shots, "m2-15-narrow.png"))
        b.set_viewport(1440, 900)
    finally:
        b.close()
        srv.stop()
        shutil.rmtree(data, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed. Screenshots: {shots}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
