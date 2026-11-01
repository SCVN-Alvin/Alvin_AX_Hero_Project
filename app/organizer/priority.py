"""Default priority policy.

Impact and urgency are stored separately. The policy only produces a *suggestion*;
a manual override is stored in its own column and never replaced by the policy.

  Critical / High impact -> P1      Medium -> P2      Low -> P3
  Unassessed impact      -> no suggestion (shown as Not assessed)
  High urgency promotes the suggestion by one tier, up to P1.
  P0 is never suggested; it requires an explicit manual override.
"""

_BASE = {"critical": "p1", "high": "p1", "medium": "p2", "low": "p3"}
_PROMOTE = {"p3": "p2", "p2": "p1", "p1": "p1"}
_IMPACT_LABEL = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}


def suggest(impact, urgency):
    """Return (suggested_priority_or_None, rationale)."""
    base = _BASE.get(impact)
    if base is None:
        return None, "Impact is unassessed, so no priority is suggested."
    reason = f"Impact {_IMPACT_LABEL[impact]} suggests {base.upper()} under the default policy."
    if urgency == "high":
        promoted = _PROMOTE[base]
        if promoted != base:
            reason += f" High urgency promotes it to {promoted.upper()}."
            return promoted, reason
        reason += " High urgency cannot promote beyond P1."
    return base, reason
