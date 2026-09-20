"""Versioned, inspectable reconciliation policy. No fuzzy joins or imputation."""

RULE_VERSION = "1.0.0"

COLUMNS = (
    "season", "game_id", "game_date", "home_team", "away_team", "home_goals",
    "away_goals", "home_shots", "away_shots", "status", "venue", "updated_at",
)
STAT_FIELDS = ("home_goals", "away_goals", "home_shots", "away_shots")
COMPARISON_FIELDS = tuple(c for c in COLUMNS if c != "updated_at")

# Deliberately closed vocabulary: an unfamiliar team remains its own identity.
# These fictional names belong only to the bundled synthetic league.
TEAM_ALIASES = {
    "R. Comets": "Rivergate Comets",
    "Rivergate C.": "Rivergate Comets",
    "N. Foxes": "Northfield Foxes",
    "M. Owls": "Millpond Owls",
    "Maplebridge M.": "Maplebridge Meteors",
    "S. Sparks": "Stonehaven Sparks",
    "C. Drift": "Cedarvale Drift",
}

RULES = [
    {"id": "IDENTITY_INVALID", "title": "Valid shared identity", "severity": "critical",
     "description": "Season must be consecutive YYYY-YY and game_id 1–64 ASCII letters, digits, dots, underscores or hyphens, starting with a letter or digit. Invalid identities are quarantined separately; names and dates never supply a guessed join."},
    {"id": "ROW_INVALID", "title": "Strict record validation", "severity": "critical",
     "description": "Require ISO calendar dates, timezone-aware ISO timestamps, nonempty teams/venue, final or scheduled status, and nonnegative integer statistics. Finals require all statistics; scheduled rows may omit all four, never a partial set. Invalid rows remain visible."},
    {"id": "SCORE_EXCEEDS_SHOTS", "title": "Goals cannot exceed shots", "severity": "critical",
     "description": "For either team, goals must not exceed shots. A plausible score does not override an impossible underlying shot count."},
    {"id": "TEAM_COLLISION", "title": "Two distinct teams", "severity": "critical",
     "description": "Home and away teams must differ after documented alias and whitespace normalization."},
    {"id": "CONFLICTING_DUPLICATE", "title": "One result per source", "severity": "critical",
     "description": "Multiple different substantive records for the same season/game_id within one feed require review. Identical substantive duplicates collapse with every source row retained in provenance; timestamps alone do not change a result."},
    {"id": "FIELD_CONFLICT", "title": "Agreement between sources", "severity": "critical",
     "description": "Both feeds must agree on every canonical field except updated_at after safe normalization. Never average a disputed score, merge fields, or assume the newest source is correct."},
    {"id": "MISSING_SOURCE", "title": "Both sources present", "severity": "warning",
     "description": "Every season/game_id must appear in both exports. Similar team names or dates do not justify pairing records with different IDs."},
]
