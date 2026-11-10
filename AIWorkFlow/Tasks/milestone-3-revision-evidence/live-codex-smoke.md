# Deliberate live Codex CLI smoke test (2026-11-04)

Run through the application's own adapter (`CodexCliAdapter`, the same code path as the interface), from the Python backend's execution environment, on this machine. **Input was the advisor's synthetic example only** (`AIWorkFlow/Architecture/AIIntegration/example-request.json`: two invented sentences about a hammer and a log, one invented clip caption). No project data, no real brief text and no files were sent.

## Connection check (what Settings → Check connection reports)

- `codex-cli 0.166.0`, installed and executable
- signed in: **yes**, auth mode **ChatGPT** (from `codex login status`; no credential file was read)
- required options present: `--output-schema --output-last-message --sandbox --ephemeral --ignore-user-config --skip-git-repo-check --ignore-rules --json`
- restrictions applied: read-only sandbox, approval policy never, ephemeral session, user config and rules not loaded, no MCP servers, web search off, empty temporary working folder, and these features off: shell tool, unified exec, code mode host/interrupt, apps, plugins, hooks, browser use (3 variants), in-app browser and automation, computer use, image generation, memories, view image, tool suggest, multi-agent, goals, skill search, sleep tool, write-stdin approval (those this installation lists)

## Request and answer

| | |
| --- | --- |
| Elapsed | 9.2 s (first run 7.9 s) |
| Reported model | not present in the event stream (`reported_model` stays empty; requested model was the account default) |
| Exit | 0; answer written only to the `--output-last-message` file |
| Validation | passed the application's own validator (shape, enums, request id, every id supplied) with no issues |
| Event types | `thread.started`, `item.completed/agent_message` (1), `turn.started`, `turn.completed`, plus one `error` item that is only the notice "Code Mode is unavailable because code-mode host is disabled … will fail closed" (expected: it is switched off on purpose). **No command, file or tool-call items.** |
| Working folder | contained only `request.json`, `schema.json`, `answer.json`, `events.jsonl`, `stderr.txt`; removed afterwards |
| Tokens | 13,191 input, 144 output |

Validated answer (abridged): one task, “Implement hammer rotation and log impact behavior”, description “Make the hammer move in a looping rotation. After impact, the selected log falls into the waterfall.”, disciplines `dev`, category `core_gameplay`, impact `unassessed`, cited both supplied blocks, `suggested_primary_reference_id: example-ref-1`, no subtasks, no questions. The impact stayed `unassessed`, as instructed.

## What this does and does not establish

- The connection, sign-in, argument-array invocation, stdin delivery, schema-constrained answer capture and validation work end to end with a real Codex CLI.
- It does **not** establish the account's model entitlement or remaining allowance (one request succeeded), or behaviour for a large brief.
- An earlier run showed two deprecation notices for `web_search_request` and `web_search_cached` and that **web search is on by default**; those two feature flags were removed from the command and `web_search="disabled"` was added. The final configuration was re-run successfully.

## Probe of the restricted runtime (synthetic)

With the final restrictions, the helper was asked to list every tool it can call. It reported `functions.wait`, `functions.request_user_input(_async)`, `functions.exec`, `collaboration.*` (spawn/send/wait/list agents), `tools.apply_patch`, `tools.list_mcp_resources`, `tools.read_mcp_resource`, `tools.clock__curr_time`, and said it **cannot run shell commands and cannot search the web**. So some built-in tools remain registered and cannot be switched off with this CLI version; the code-mode host is off (it fails closed). The read-only sandbox, never-ask approvals, an empty working folder and no MCP servers limit what they can do, but a prompt is not an isolation boundary. **This is a trusted personal pilot**, as the brief allows; a direct API adapter would isolate input more strictly.
