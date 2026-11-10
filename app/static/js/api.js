// Thin fetch wrapper plus visible persistence state ("Saving…", "Saved", "Save failed").
export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details || {};
  }
}

const listeners = new Set();
// status is derived: a failure stays visible (sticky) until the user dismisses it
// or a retry of the failed save succeeds, even if other writes succeed meanwhile.
export const saveState = { status: "idle", pending: 0, failure: "", savedAt: null };

export function onSaveState(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
function update() {
  saveState.status = saveState.failure ? "failed" : saveState.pending > 0 ? "saving" : saveState.savedAt ? "saved" : "idle";
  for (const fn of listeners) fn(saveState);
}
export function clearFailure() {
  if (saveState.failure) {
    saveState.failure = "";
    saveState.savedAt = saveState.savedAt || Date.now();
    update();
  }
}

// Expected prompts, not failures: the caller asks the user for a decision.
const NOT_FAILURES = new Set(["unfinished_children"]);

// `quiet` requests (previews, starting a background job) report their own errors where they are
// used; they are not saves, so they never show the sticky "Not saved" state or block leaving the page.
async function request(method, path, body, opts = {}) {
  const write = method !== "GET" && !opts.quiet;
  if (write) {
    saveState.pending += 1;
    update();
  }
  try {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: body !== undefined ? { "Content-Type": "application/json" } : {},
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch {
      throw new ApiError(0, "network", "Cannot reach the local service. Is it still running?");
    }
    let data = null;
    try {
      data = await res.json();
    } catch {
      /* non-JSON body */
    }
    if (!res.ok) {
      const e = data && data.error;
      throw new ApiError(res.status, (e && e.code) || "error", (e && e.message) || `Request failed (${res.status})`, e && e.details);
    }
    if (write) {
      saveState.pending -= 1;
      saveState.savedAt = Date.now();
      update();
    }
    return data;
  } catch (err) {
    if (write) {
      saveState.pending -= 1;
      if (!NOT_FAILURES.has(err.code)) saveState.failure = err.message || "Save failed";
      update();
    }
    throw err;
  }
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b = {}, opts) => request("POST", p, b, opts),
  patch: (p, b) => request("PATCH", p, b),
  del: (p, b) => request("DELETE", p, b),
};

export function qs(params) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) {
      if (v.length) sp.set(k, v.join(","));
    } else if (v) sp.set(k, v);
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}
