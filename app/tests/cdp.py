"""Tiny Chrome DevTools Protocol client (standard library only) for UI verification.

Launches a headless Chrome, drives it over a WebSocket, and records console errors
and every network request so offline behaviour can be asserted.
"""

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class WS:
    def __init__(self, url):
        assert url.startswith("ws://")
        hostport, path = url[5:].split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(1)
        assert b" 101 " in buf.split(b"\r\n")[0], buf

    def send(self, text):
        data = text.encode()
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("websocket closed")
            buf += chunk
        return buf

    def recv(self):
        message = b""
        while True:
            b1, b2 = self._read(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            if opcode == 0x8:
                raise ConnectionError("websocket closed")
            if opcode in (0x9, 0xA):
                continue
            message += payload
            if fin:
                return message.decode()


class Browser:
    def __init__(self, width=1440, height=900, port=9333):
        self.profile = tempfile.mkdtemp(prefix="organizer-chrome-")
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={self.profile}",
             "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync",
             "--disable-background-networking", f"--window-size={width},{height}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                page = next(t for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError("Chrome did not start")
        self.ws = WS(page["webSocketDebuggerUrl"])
        self._id = 0
        self.console_errors = []
        self.requests = []
        self.events = []
        self.drag_data = None
        for domain in ("Page", "Runtime", "Network", "Log"):
            self.call(f"{domain}.enable")
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)  # deliver focus/blur like a foreground window
        self.set_viewport(width, height)

    def set_viewport(self, width, height):
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)

    def call(self, method, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            self._event(msg)

    def _event(self, msg):
        m = msg.get("method")
        p = msg.get("params", {})
        if m == "Runtime.consoleAPICalled" and p.get("type") in ("error", "assert"):
            self.console_errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", [])))
        elif m == "Runtime.exceptionThrown":
            d = p["exceptionDetails"]
            self.console_errors.append("EXCEPTION " + (d.get("exception", {}).get("description") or d.get("text", "")))
        elif m == "Log.entryAdded" and p["entry"]["level"] == "error":
            self.console_errors.append("LOG " + p["entry"]["text"] + " " + p["entry"].get("url", ""))
        elif m == "Input.dragIntercepted":
            self.drag_data = p["data"]
        elif m == "Network.requestWillBeSent":
            self.requests.append(p["request"]["url"])

    def pump(self, seconds=0.3):
        end = time.time() + seconds
        self.ws.sock.settimeout(0.1)
        try:
            while time.time() < end:
                try:
                    self._event(json.loads(self.ws.recv()))
                except socket.timeout:
                    pass
        finally:
            self.ws.sock.settimeout(30)

    def goto(self, url):
        self.call("Page.navigate", url=url)
        self.pump(0.8)

    def js(self, expression, await_promise=True):
        r = self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=await_promise)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", r["exceptionDetails"]))
        return r["result"].get("value")

    def wait_for(self, selector, timeout=5.0, visible=True):
        end = time.time() + timeout
        while time.time() < end:
            ok = self.js(f"(() => {{ const e = document.querySelector({json.dumps(selector)}); return !!e && (!{str(visible).lower()} || !e.hidden); }})()")
            if ok:
                return True
            self.pump(0.1)
        raise AssertionError(f"Timed out waiting for {selector}")

    def click(self, selector, index=0):
        self.js(f"document.querySelectorAll({json.dumps(selector)})[{index}].click()")
        self.pump(0.35)

    def type(self, selector, text, index=0):
        self.js(f"""(() => {{ const e = document.querySelectorAll({json.dumps(selector)})[{index}]; e.focus(); e.value = {json.dumps(text)};
            e.dispatchEvent(new Event('input', {{bubbles: true}})); }})()""")
        self.pump(0.1)

    def key(self, key, modifiers=0, code=None, vk=None):
        for typ in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=typ, key=key, code=code or key, modifiers=modifiers,
                      windowsVirtualKeyCode=vk or 0)
        self.pump(0.35)

    def native_drag(self, start, end, steps=8):
        """Drag with real mouse input: Chrome fires the genuine dragstart/dragover/drop sequence."""
        self.drag_data = None
        self.call("Input.setInterceptDrags", enabled=True)
        self.call("Input.dispatchMouseEvent", type="mouseMoved", x=start[0], y=start[1])
        self.call("Input.dispatchMouseEvent", type="mousePressed", x=start[0], y=start[1], button="left", buttons=1, clickCount=1)
        for i in range(1, steps + 1):
            x = start[0] + (end[0] - start[0]) * i / steps
            y = start[1] + (end[1] - start[1]) * i / steps
            self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, button="left", buttons=1)
            self.pump(0.05)
            if self.drag_data:
                break
        if not self.drag_data:
            self.call("Input.setInterceptDrags", enabled=False)
            raise AssertionError("Browser never started a native drag (card not draggable?)")
        for kind in ("dragEnter", "dragOver", "drop"):
            self.call("Input.dispatchDragEvent", type=kind, x=end[0], y=end[1], data=self.drag_data)
            self.pump(0.1)
        self.call("Input.dispatchMouseEvent", type="mouseReleased", x=end[0], y=end[1], button="left", buttons=0, clickCount=1)
        self.call("Input.setInterceptDrags", enabled=False)
        self.pump(0.6)

    def screenshot(self, path):
        data = self.call("Page.captureScreenshot", format="png")["data"]
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(data))

    def close(self):
        try:
            self.call("Browser.close")
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)
