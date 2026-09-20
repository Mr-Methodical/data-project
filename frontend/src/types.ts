export type SourceId = "scorer" | "league";
export type Game = {
  key: string;
  season: string;
  game_id: string;
  game_date: string;
  home_team: string;
  away_team: string;
  home_goals: number | null;
  away_goals: number | null;
  home_shots: number | null;
  away_shots: number | null;
  status: string;
  venue: string;
  updated_at: string;
  provenance: string[];
};
export type Normalization = {
  source?: string;
  row_number?: number;
  field: string;
  before: unknown;
  after: unknown;
  reason: string;
};
export type Candidate = {
  id: string;
  source: SourceId;
  row_number: number;
  key: string;
  raw: Record<string, unknown>;
  normalized: Game;
  errors: { rule_id: string; field: string; message: string }[];
  normalizations: Normalization[];
  valid: boolean;
};
export type Case = {
  id: string;
  key: string;
  title: string;
  kind: "conflict" | "invalid" | "duplicate" | "missing";
  severity: "critical" | "warning";
  rule_ids: string[];
  description: string;
  fields: string[];
  candidate_ids: string[];
  fingerprint: string;
  status: "open" | "resolved";
  version: number;
  resolution: null | {
    action: "select" | "exclude";
    candidate_id?: string;
    note: string;
    reviewer: string;
    created_at: string;
    replayed_from?: unknown;
  };
};
export type Rule = {
  id: string;
  title: string;
  description: string;
  severity: string;
};
export type Source = {
  id: SourceId;
  label: string;
  row_count: number;
  sha256: string;
};
export type Standing = {
  team: string;
  played: number;
  wins: number;
  losses: number;
  ties: number;
  goals_for: number;
  goals_against: number;
  goal_difference: number;
  points: number;
};
export type AuditEvent = {
  id: string;
  run_id: string;
  case_id: string | null;
  action: string;
  reviewer: string;
  note: string;
  created_at: string;
  details: Record<string, unknown>;
  previous_hash: string;
  event_hash: string;
};
export type RunSummary = {
  id: string;
  name: string;
  created_at: string;
  rule_version: string;
  game_count: number;
  input_rows: number;
  open_cases: number;
  resolved_cases: number;
  excluded_games: number;
  published_games: number;
  auto_matched: number;
  quality_score: number;
  is_demo: boolean;
};
export type RunDetail = RunSummary & {
  sources: Source[];
  candidates: Candidate[];
  cases: Case[];
  canonical: Game[];
  standings: Standing[];
  normalizations: Normalization[];
  stats: {
    input_rows: number;
    game_count: number;
    auto_matched: number;
    normalization_count: number;
    exact_duplicates: number;
  };
  rules: Rule[];
  audit: AuditEvent[];
  replay_available: number;
};
export type Preview = {
  before: { published_games: number; standings: Standing[] };
  after: { published_games: number; standings: Standing[] };
  selected: Game | null;
  excluded: boolean;
  changes: { field: string; before: unknown; after: unknown }[];
};
export type View =
  "overview" | "queue" | "canonical" | "lineage" | "audit" | "rules";
