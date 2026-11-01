"""Enumerations shared by the schema, the service layer, and the interface."""

STATUSES = [
    ("backlog", "Backlog"),
    ("to_do", "To Do"),
    ("in_progress", "In Progress"),
    ("in_review", "In Review"),
    ("done", "Done"),
]
CATEGORIES = [
    ("core_gameplay", "Core gameplay"),
    ("content", "Content"),
    ("ui_ux", "UI/UX"),
    ("polish", "Polish"),
    ("bug_fix", "Bug fix"),
    ("technical", "Technical"),
    ("production", "Production"),
    ("unassigned", "Unassigned"),
]
IMPACTS = [
    ("critical", "Critical"),
    ("high", "High"),
    ("medium", "Medium"),
    ("low", "Low"),
    ("unassessed", "Unassessed"),
]
URGENCIES = [
    ("high", "High"),
    ("medium", "Medium"),
    ("low", "Low"),
    ("unspecified", "Unspecified"),
]
PRIORITIES = [("p0", "P0"), ("p1", "P1"), ("p2", "P2"), ("p3", "P3")]
LEVELS = [("epic", "Epic"), ("task", "Task"), ("subtask", "Subtask")]
DISCIPLINES = [("art", "Art"), ("dev", "Dev")]

STATUS_KEYS = [k for k, _ in STATUSES]
CATEGORY_KEYS = [k for k, _ in CATEGORIES]
IMPACT_KEYS = [k for k, _ in IMPACTS]
URGENCY_KEYS = [k for k, _ in URGENCIES]
PRIORITY_KEYS = [k for k, _ in PRIORITIES]
LEVEL_KEYS = [k for k, _ in LEVELS]
DISCIPLINE_KEYS = [k for k, _ in DISCIPLINES]

# epic < task < subtask. A child must rank strictly below its parent.
LEVEL_RANK = {"epic": 0, "task": 1, "subtask": 2}


def as_meta():
    def pairs(items):
        return [{"value": k, "label": v} for k, v in items]

    return {
        "statuses": pairs(STATUSES),
        "categories": pairs(CATEGORIES),
        "impacts": pairs(IMPACTS),
        "urgencies": pairs(URGENCIES),
        "priorities": pairs(PRIORITIES),
        "levels": pairs(LEVELS),
        "disciplines": pairs(DISCIPLINES),
    }
