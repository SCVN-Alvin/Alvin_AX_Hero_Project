"""Adapters between the application and a model runtime.

One internal contract: `run(request, instructions, schema_text, cancel, timeout) -> AdapterResult`, plus `check()`.
`CodexCliAdapter` drives the signed-in Codex command line tool; `FakeAdapter` is a scripted stand-in used by tests and
demonstrations. A future API adapter would implement the same two methods.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time

# The Codex features switched off for a drafting request, so the model can only read the request and answer.
# Only features this installation actually lists are passed (an unknown name would make the CLI refuse to run).
RESTRICT_FEATURES = ("shell_tool", "unified_exec", "unified_exec_tty", "code_mode_host", "code_mode_interrupt", "apps", "plugins", "remote_plugin", "hooks", "browser_use",
                     "browser_use_external", "browser_use_full_cdp_access", "in_app_browser", "in_app_local_automation", "computer_use", "image_generation", "memories",
                     "view_image", "tool_suggest", "multi_agent", "goals", "skill_search", "sleep_tool", "write_stdin_approval")
REQUIRED_FLAGS = ("--output-schema", "--output-last-message", "--sandbox", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check", "--ignore-rules", "--json")
# Never inherited by the helper: an API key in the environment would silently switch a subscription sign-in to billed usage.
SECRET_ENV = re.compile(r"(API_KEY|TOKEN|SECRET|PASSWORD|OPENAI|ANTHROPIC)", re.I)
PASS_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "CODEX_HOME", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy",
            "http_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE")

MESSAGES = {
    "disabled": "AI drafting is turned off. Turn it on in Settings first.",
    "cli_missing": "The Codex command-line tool was not found. Install Codex, or set its location in Settings.",
    "unsupported_cli": "This Codex installation does not support the options the organizer needs.",
    "not_signed_in": "Codex is not signed in. In a terminal run “codex login” and finish the browser sign-in, then try again. The organizer never asks for your password.",
    "usage_limit": "Your Codex usage allowance has been reached. Try again later. Nothing in your project was changed.",
    "network": "Codex could not reach the internet. Check your connection and try again. Nothing in your project was changed.",
    "timeout": "The request took longer than the time limit and was stopped. Nothing in your project was changed.",
    "cancelled": "Cancelled. Nothing in your project was changed.",
    "empty_output": "The AI returned nothing. Nothing in your project was changed; you can try again.",
    "invalid_output": "The AI's answer did not pass the checks, so it was not used. Nothing in your project was changed.",
    "adapter_error": "The AI helper stopped with an error. Nothing in your project was changed.",
    "interrupted": "The service stopped before this finished. Nothing was imported; you can try again.",
}


class AdapterError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail

    @property
    def message(self):
        m = MESSAGES.get(self.code, MESSAGES["adapter_error"])
        return f"{m} ({self.detail})" if self.detail and self.code in ("adapter_error", "unsupported_cli") else m


class AdapterResult:
    def __init__(self, text, model="", version=""):
        self.text, self.model, self.version = text, model, version


def classify_failure(text):
    """Map the CLI's own words onto a stable error code. The raw text is never shown as a credential or path."""
    t = (text or "").lower()
    if any(k in t for k in ("not logged in", "please log in", "run `codex login`", "codex login", "unauthorized", "401", "authentication", "sign in", "invalid api key", "token expired", "refresh token")):
        return "not_signed_in"
    if any(k in t for k in ("usage limit", "rate limit", "rate_limit", "quota", "too many requests", "429", "limit reached", "hit your limit", "exceeded your")):
        return "usage_limit"
    if any(k in t for k in ("network", "dns", "connection refused", "connection reset", "could not resolve", "unreachable", "offline", "error sending request", "timed out connecting")):
        return "network"
    return "adapter_error"


def _tail(text, n=300):
    text = " ".join((text or "").split())
    return text[-n:]


# ---- Codex CLI ------------------------------------------------------------------

class CodexCliAdapter:
    name = "codex_cli"

    def __init__(self, binary="codex", model="", work_root=None):
        self.binary = binary or "codex"
        self.model = model or ""
        self.work_root = work_root

    # -- discovery
    def resolve(self):
        path = self.binary if os.sep in self.binary else shutil.which(self.binary)
        if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
            raise AdapterError("cli_missing")
        return path

    def _quick(self, args, timeout=20):
        try:
            p = subprocess.run([self.resolve(), *args], shell=False, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                               env=self._env(), cwd=tempfile.gettempdir())
        except subprocess.TimeoutExpired:
            raise AdapterError("timeout")
        except OSError as exc:
            raise AdapterError("cli_missing", str(exc))
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    def check(self):
        """Report what this installation can do, without reading any credential file or sending any content."""
        out = {"provider": "codex_cli", "available": False, "version": "", "signed_in": False, "auth_mode": "", "flags_ok": False,
               "missing_flags": [], "restrictions": [], "message": "",
               "limitations": ["Codex still lists some built-in tools that this version cannot switch off. The read-only sandbox, the never-ask approval policy and an empty "
                               "working folder limit what they can do, but a prompt is not an isolation boundary.",
                               "Treat this as a trusted personal pilot. Only the selected text and captions are sent, and nothing in your project is writable by the helper. "
                               "A direct API adapter would isolate the input more strictly."]}
        try:
            self.resolve()
        except AdapterError as exc:
            out["message"] = exc.message
            return out
        try:
            code, text = self._quick(["--version"])
            out["version"] = text.strip().splitlines()[0] if text.strip() else ""
            out["available"] = code == 0
            code, text = self._quick(["login", "status"])
            low = text.lower()
            out["signed_in"] = code == 0 and "logged in" in low
            out["auth_mode"] = "chatgpt" if "chatgpt" in low else "api_key" if "api key" in low else ("other" if out["signed_in"] else "")
            code, text = self._quick(["exec", "--help"])
            out["missing_flags"] = [f for f in REQUIRED_FLAGS if f not in text]
            out["flags_ok"] = code == 0 and not out["missing_flags"]
            code, text = self._quick(["features", "list"])
            known = {line.split()[0] for line in text.splitlines() if line.strip()} if code == 0 else set()
            out["restrictions"] = ["read-only sandbox", "approval policy never", "ephemeral session", "your Codex config and rules are not loaded", "no MCP servers", "web search off",
                                   "an empty temporary working folder"] + [f"{f.replace('_', ' ')} off" for f in self._features(known)]
        except AdapterError as exc:
            out["message"] = exc.message
            return out
        if not out["flags_ok"]:
            out["message"] = MESSAGES["unsupported_cli"] + " Missing: " + ", ".join(out["missing_flags"])
        elif not out["signed_in"]:
            out["message"] = MESSAGES["not_signed_in"]
        else:
            out["message"] = "Ready. Signed in with " + ("a ChatGPT account." if out["auth_mode"] == "chatgpt" else "an API key." if out["auth_mode"] == "api_key" else "Codex.")
            out["note"] = "Sign-in is confirmed; the model you can use and your remaining allowance are only known when a request is made."
        return out

    @staticmethod
    def _features(known):
        return [f for f in RESTRICT_FEATURES if f in known] if known else []

    def _env(self):
        env = {k: v for k, v in os.environ.items() if k in PASS_ENV and not SECRET_ENV.search(k)}
        env.setdefault("PATH", "/usr/bin:/bin:/usr/local/bin")
        return env

    # -- the request
    def command(self, tmp, schema_path, out_path, instructions, features):
        cmd = [self.resolve(), "--ask-for-approval", "never", "exec", "--json", "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only",
               "--ephemeral", "--skip-git-repo-check", "--color", "never", "-C", tmp, "-c", "mcp_servers={}", "-c", 'web_search="disabled"']
        for f in features:
            cmd += ["--disable", f]
        cmd += ["--output-schema", schema_path, "--output-last-message", out_path]
        if self.model:
            cmd += ["-m", self.model]
        cmd.append(instructions)
        return cmd

    def run(self, request, instructions, schema_text, cancel, timeout):
        info = self.check()
        if not info["available"]:
            raise AdapterError("cli_missing")
        if not info["flags_ok"]:
            raise AdapterError("unsupported_cli", "missing " + ", ".join(info["missing_flags"]))
        if not info["signed_in"]:
            raise AdapterError("not_signed_in")
        _, text = self._quick(["features", "list"])
        known = {line.split()[0] for line in text.splitlines() if line.strip()}
        tmp = tempfile.mkdtemp(prefix="organizer-ai-", dir=self.work_root)
        proc = None
        try:
            os.chmod(tmp, 0o700)
            schema_path, out_path, req_path = (os.path.join(tmp, n) for n in ("schema.json", "answer.json", "request.json"))
            with open(schema_path, "w", encoding="utf-8") as fh:
                fh.write(schema_text)
            with open(req_path, "w", encoding="utf-8") as fh:
                json.dump(request, fh, ensure_ascii=False)
            cmd = self.command(tmp, schema_path, out_path, instructions, self._features(known))
            with open(req_path, "rb") as stdin, open(os.path.join(tmp, "events.jsonl"), "wb") as events, open(os.path.join(tmp, "stderr.txt"), "wb") as err:
                proc = subprocess.Popen(cmd, shell=False, stdin=stdin, stdout=events, stderr=err, env=self._env(), cwd=tmp, start_new_session=True)
                deadline = time.monotonic() + timeout
                while proc.poll() is None:
                    if cancel.is_set():
                        self._kill(proc)
                        raise AdapterError("cancelled")
                    if time.monotonic() > deadline:
                        self._kill(proc)
                        raise AdapterError("timeout")
                    time.sleep(0.2)
            events_text = self._read(os.path.join(tmp, "events.jsonl"))
            err_text = self._read(os.path.join(tmp, "stderr.txt"))
            answer = self._read(out_path)
            if proc.returncode != 0:
                code = classify_failure(err_text + "\n" + events_text)
                raise AdapterError(code, _tail(err_text or events_text))
            if not answer.strip():
                code = classify_failure(err_text + "\n" + events_text)
                raise AdapterError("empty_output" if code == "adapter_error" else code, _tail(err_text))
            return AdapterResult(answer, model=self._model_from(events_text), version=info["version"])
        finally:
            if proc is not None and proc.poll() is None:
                self._kill(proc)
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _read(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    @staticmethod
    def _kill(proc):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait(timeout=3)

    @staticmethod
    def _model_from(events_text):
        for line in events_text.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            found = _find_key(ev, "model")
            if isinstance(found, str) and found:
                return found[:100]
        return ""


def _find_key(obj, key, depth=0):
    if depth > 4:
        return None
    if isinstance(obj, dict):
        if isinstance(obj.get(key), str):
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key, depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj[:20]:
            r = _find_key(v, key, depth + 1)
            if r:
                return r
    return None


# ---- scripted adapter for tests and demonstrations ------------------------------------

class FakeAdapter:
    """Answers from a script instead of a model. The script is re-read on every request so a test can change it.

    Script keys: mode (ok, invalid_json, empty, unknown_ids, wrong_request_id, invented_value, auth, usage_limit, network, error, slow, hang, crash),
    delay (seconds), and tasks/questions written against labels and words instead of ids, for example
    {"match_label": "Log hammering", "title": "...", "description": "...", "cite": "all", "cite_containing": ["hammer"],
     "primary_reference_containing": "hammer"}.
    """

    name = "fake"

    def __init__(self, script=None, script_path=None):
        self.script, self.script_path = script, script_path
        self.calls = []

    def _load(self):
        if self.script_path:
            try:
                with open(self.script_path, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                return {}
        return self.script or {}

    def check(self):
        return {"provider": "fake", "available": True, "version": "fake-adapter 1", "signed_in": True, "auth_mode": "none", "flags_ok": True, "missing_flags": [],
                "restrictions": ["scripted answers; nothing is sent anywhere"], "message": "Ready (scripted test adapter)."}

    def run(self, request, instructions, schema_text, cancel, timeout):
        script = self._load()
        self.calls.append({"request": request, "instructions": instructions, "schema": schema_text})
        mode = script.get("mode", "ok")
        delay = 10 ** 6 if mode == "hang" else float(script.get("delay", 0))
        started = time.monotonic()
        while time.monotonic() - started < delay:
            if cancel.is_set():
                raise AdapterError("cancelled")
            if time.monotonic() - started > timeout:
                raise AdapterError("timeout")
            time.sleep(0.05)
        if cancel.is_set():
            raise AdapterError("cancelled")
        if mode == "auth":
            raise AdapterError("not_signed_in")
        if mode == "usage_limit":
            raise AdapterError("usage_limit")
        if mode == "network":
            raise AdapterError("network")
        if mode == "error":
            raise AdapterError("adapter_error", "scripted failure")
        if mode == "empty":
            return AdapterResult("", "fake-model", "fake-adapter 1")
        if mode == "invalid_json":
            return AdapterResult('{"request_id": "' + request["request_id"] + '", "tasks": [', "fake-model", "fake-adapter 1")
        return AdapterResult(json.dumps(self._answer(request, script, mode), ensure_ascii=False), script.get("model", "fake-model"), "fake-adapter 1")

    def _answer(self, request, script, mode):
        blocks = request["source_blocks"]
        refs = request["reference_manifest"]

        def cite(spec):
            ids = []
            if spec.get("cite") == "all":
                ids = [b["id"] for b in blocks]
            for word in spec.get("cite_containing", []):
                ids += [b["id"] for b in blocks if word.lower() in b["text"].lower()]
            if spec.get("cite_system"):
                for s in request.get("systems", []):
                    if s["label"] == spec["cite_system"]:
                        ids += s["source_block_ids"]
            return list(dict.fromkeys(ids))

        tasks, n = [], 0
        specs = script.get("tasks")
        if specs is None:   # default: one tidy task per system, citing its own blocks
            specs = [{"match_label": s["label"], "title": f"Implement {s['label'].lower()}", "description": f"Build {s['label'].lower()} as described in the cited source lines.",
                      "cite_system": s["label"]} for s in request.get("systems", [])]
        labels = {s["label"] for s in request.get("systems", [])}
        for spec in specs:
            if spec.get("match_label") and spec["match_label"] not in labels:
                continue
            n += 1
            system = next((s for s in request.get("systems", []) if s["label"] == spec.get("match_label")), None)
            spec = dict(spec, cite_system=spec.get("cite_system") or (system["label"] if system else None))
            ids = cite(spec) or [b["id"] for b in blocks][:1]
            pref = None
            if spec.get("primary_reference_containing"):
                pref = next((r["id"] for r in refs if spec["primary_reference_containing"].lower() in r["caption"].lower()), None)
            subs = []
            for j, s in enumerate(spec.get("subtasks", [])):
                sub = {"draft_id": f"d{n}.{j + 1}", "title": s["title"], "description": s.get("description", ""), "basis": s.get("basis", "source_supported"),
                       "source_block_ids": cite(s) if (s.get("cite") or s.get("cite_containing")) else ([] if s.get("basis") == "inferred" else ids[:1]),
                       "scenario_ids": [x for x in s.get("scenario_ids", []) if x in request["scenario_ids"]]}
                subs.append(sub)
            task = {"draft_id": f"d{n}", "title": spec["title"], "description": spec.get("description", ""), "disciplines": spec.get("disciplines", []),
                    "category": spec.get("category", "unassigned"), "impact": spec.get("impact", "unassessed"),
                    "classification_reason": spec.get("classification_reason", ""), "source_block_ids": ids,
                    "scenario_ids": [x for x in spec.get("scenario_ids", []) if x in request["scenario_ids"]] if "scenario_ids" in spec else list(system["scenario_ids"]) if system else [],
                    "suggested_primary_reference_id": pref, "subtasks": subs}
            tasks.append(task)
        questions = [{"text": q["text"], "source_block_ids": cite(q) or [blocks[0]["id"]]} for q in script.get("questions", []) if blocks]
        out = {"request_id": request["request_id"], "tasks": tasks, "questions": questions}
        if mode == "unknown_ids" and tasks:
            tasks[0]["source_block_ids"] = tasks[0]["source_block_ids"] + ["b999"]
        if mode == "unknown_reference" and tasks:
            tasks[0]["suggested_primary_reference_id"] = "r999"
        if mode == "unknown_scenario" and tasks:
            tasks[0]["scenario_ids"] = ["777"]
        if mode == "wrong_request_id":
            out["request_id"] = "someone-else"
        if mode == "invented_value" and tasks:
            tasks[0]["description"] += " The reward is 4242 coins."
        if mode == "bad_enum" and tasks:
            tasks[0]["category"] = "fun"
        if mode == "duplicate_ids" and len(tasks) > 0:
            tasks.append(dict(tasks[0], title=tasks[0]["title"] + " again"))
        if mode == "extra_field" and tasks:
            tasks[0]["priority"] = "p0"
        if mode == "no_evidence" and tasks:
            tasks[0]["source_block_ids"] = []
        return out
