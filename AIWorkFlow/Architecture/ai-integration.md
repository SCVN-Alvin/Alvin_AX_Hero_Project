# AI Integration Implementation Brief

Add an optional AI stage after local filtering and before candidate review. The local Python service prepares a bounded request, calls an adapter, validates the structured result, and presents proposed tasks. A person still selects the primary reference and approves board tickets.

The user approved this direction on November 3, 2026. Implement the existing Milestone 3 correctness fixes and the concise task presentation before accepting the AI-assisted workflow.

## Account and connection choice

The advisor checked the local installation on November 3: `codex --version` reports `codex-cli 0.166.0`, and `codex login status` reports `Logged in using ChatGPT`. No credential file was inspected. This establishes local sign-in status, not model entitlement, remaining allowance, or a successful inference request.

Use a Codex CLI adapter for the personal pilot. OpenAI documents non-interactive `codex exec`, schema-constrained responses, and user-account automation. General automation is normally directed toward API keys; this pilot specifically needs the existing Codex account. [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).

ChatGPT sign-in and API-key sign-in are distinct authentication modes. Keep credentials managed by Codex; do not copy its cached tokens into the application or treat them as Platform API keys. [Authentication](https://learn.chatgpt.com/docs/auth?surface=cli).

For a later conventional API adapter, use Platform credentials and API usage settings. Eligible ChatGPT Pro or Business subscribers may activate API credits by linking an eligible subscription to an API organization they own; eligibility and activation have not been checked for this account. [API credits](https://developers.openai.com/api/docs/guides/chatgpt-api-credits).

A separate supported Sign in with ChatGPT integration exists for applications using Codex app-server, but it requires its own OAuth/session lifecycle. Consider it for a richer client later, rather than extracting the current CLI session. [Codex app-server integration](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server).

## Application flow

1. Select a source system or small batch and filter out irrelevant or historical material locally.
2. Preview included text, scenario context, and reference captions. Default to text plus a manifest; actual image content requires a separate explicit selection and a supported adapter.
3. Start an AI draft job and show progress, cancellation, and connection errors.
4. Validate the returned JSON, IDs, and field values. Present unsupported claims or conflicts for review.
5. Preview the proposed task list and let the user import selected drafts into needs-review candidates.
6. Review the short description, subtasks, classifications, and primary reference before the normal board approval step.

Keep the task-first view: one short description and one selected demonstration. The AI stage must not bring back a wall of source material or automatically attach all suggested media.

## Adapter boundary

Define one internal request and response contract for `codex_cli` and a future `openai_api` adapter. Keep browser requests local to the application service; do not place provider credentials in browser code.

Input contains a request ID, selected source block IDs and wording, scenario IDs, reference IDs/captions, and known questions. Exclude unrelated source sections and absolute local paths. Use a conservative configurable batch size and input-size limit as product limits, not claims about model capacity.

The response schema is [task-draft.schema.json](AIIntegration/task-draft.schema.json). [example-request.json](AIIntegration/example-request.json) is synthetic input, not a real clip inventory. [instructions.txt](AIIntegration/instructions.txt) contains the drafting instructions.

Proposed classifications remain suggestions. Calculate scheduling priority through the existing local policy after reviewed impact and urgency are established. Do not let the AI set status, assignee, deadline, or P0.

## CLI execution requirements

Run a configured Codex binary through an argument array using Python subprocess APIs with `shell=False`. Pass instructions as an argument and the request JSON over stdin. Do not build a shell command from document text.

Use a dedicated temporary working directory instead of the project repository or live data directory. Keep the schema and approved inputs there, capture the final response separately from event/progress output, impose a timeout, and terminate the child process on cancellation. An ephemeral run does not replace the application's own deliberate draft history.

The installed CLI supports `--output-schema`, `--output-last-message`, `--sandbox read-only`, `--ephemeral`, `--ignore-user-config`, and stdin input. Confirm compatibility on startup rather than assuming all installations match this snapshot. Keep the existing Codex-managed sign-in; do not change global account settings to perform a request.

Read-only restricts writes; it does not establish that an agent cannot read unrelated files or call tools. Require inference-only behavior, exclude inherited MCP integrations, and verify the chosen runtime configuration. Prompts alone are not a technical isolation boundary. If a sufficiently restricted CLI runtime cannot be established, keep it a trusted personal pilot and use a direct inference API adapter for stricter input isolation. Do not give this helper unrestricted command execution or access to the live application database.

## Draft validation and persistence

- Validate shape and enums independently of the CLI's schema constraint. Reject unknown source, reference, or scenario IDs and duplicate draft IDs.
- Store AI runs separately with project/document IDs, input snapshot/hash, provider, requested and reported model when available, prompt/schema versions, state, and timestamps.
- Preserve source wording. Each supported task or subtask links to supplied evidence. Mark implementation ideas with no direct source as inferred.
- A response belongs to its original project and request, even if the user switches projects while it runs. Late responses must not overwrite newer drafts.
- Importing the same response twice must not duplicate candidates. A retry creates another proposed version, not replacement board tickets.
- Add AI provenance through an explicit migration; the current candidate origin enum must not mislabel AI work as manually entered or deterministic extraction.
- Applying selected AI drafts and their references is transactional. Invalid output, timeout, cancellation, or limit errors leave existing tasks unchanged.
- Do not silently fall back from subscription-backed Codex to a billed API key or a different provider.

Keep credentials out of run history and project exports. Store only application-managed input/output content needed for review and recovery. Actual source transmission happens only when the user invokes the AI action with its selected payload visible.

## Setup and manual smoke test

On this machine sign-in is already present. To check it again:

```sh
codex --version
codex login status
```

If a different installation is signed out, the user runs `codex login` and completes the browser flow using their own ChatGPT account. The organizer must not collect a ChatGPT password. The backend process needs the same operating-system user's Codex executable and credential access.

For a manual synthetic-input trial, the following command uses the sample files in this repository and writes a response to the temporary directory. It is a command template, not a completed connection test:

```sh
codex --ask-for-approval never exec \
  --ignore-user-config \
  --sandbox read-only \
  --ephemeral \
  --skip-git-repo-check \
  --output-schema /Volumes/Supercent/AIXJam/AIWorkFlow/Architecture/AIIntegration/task-draft.schema.json \
  --output-last-message /private/tmp/aixjam-ai-task-drafts.json \
  'Use only the supplied JSON as evidence. Draft concise tasks, preserve constraints, return the required JSON, and do not use tools or read other files.' \
  < /Volumes/Supercent/AIXJam/AIWorkFlow/Architecture/AIIntegration/example-request.json
```

Run the manual trial from a dedicated empty temporary working directory. The application adapter must enforce its stronger execution and validation requirements rather than treating this command alone as isolation.

## Acceptance

Demonstrate Log hammering with filtered text, a short AI-proposed description, valid evidence IDs, and a manually selected primary clip. Demonstrate Wood shave with clean/bad variations and resource values preserved. Keep unresolved source conflicts visible.

Test unknown IDs, invented values, malformed JSON, empty output, unavailable credentials, usage limits, timeout, cancellation, concurrent project switching, duplicate import, and recovery of the previous draft. Use a fake adapter for routine tests and a deliberate selected-payload request for a live connection check. The whole non-AI workflow must work with the adapter disabled and the machine offline.
