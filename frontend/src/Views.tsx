import { useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCheck,
  ChevronRight,
  ClipboardCheck,
  Copy,
  Database,
  FileCheck2,
  FileText,
  Fingerprint,
  GitCompareArrows,
  Layers3,
  Search,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";
import type { Case, RunDetail, View } from "./types";
import { Badge, Empty, dateTime, humanize, pretty } from "./ui";

export function CasesTable({
  run,
  cases,
  onCase,
  compact = false,
}: {
  run: RunDetail;
  cases: Case[];
  onCase: (id: string) => void;
  compact?: boolean;
}) {
  if (!cases.length)
    return (
      <Empty title="Nothing waiting here">
        No cases match this view. Adjust your filters or explore the canonical
        data.
      </Empty>
    );
  return (
    <div className="table-scroll">
      <table className={`data-table cases-table ${compact ? "compact" : ""}`}>
        <thead>
          <tr>
            <th>Game / record</th>
            <th>Exception</th>
            <th>Severity</th>
            <th>Status</th>
            <th>
              <span className="sr-only">Review</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {cases.map((item) => {
            const candidate = run.candidates.find((c) =>
              item.candidate_ids.includes(c.id),
            );
            const g = candidate?.normalized;
            return (
              <tr key={item.id}>
                <td>
                  <button
                    data-testid={`review-case-${item.id}`}
                    className="table-link game-cell"
                    onClick={() => onCase(item.id)}
                  >
                    <span className="game-id">{g?.game_id || item.key}</span>
                    <strong>
                      {g?.away_team || "Unknown team"}{" "}
                      <span className="versus">vs</span>{" "}
                      {g?.home_team || "Unknown team"}
                    </strong>
                    <span className="small muted">
                      {g?.game_date || "Date needs review"}
                    </span>
                  </button>
                </td>
                <td>
                  <span className="exception-name">{humanize(item.kind)}</span>
                  <span className="table-subtext">
                    {item.fields.length
                      ? item.fields.slice(0, 2).map(humanize).join(", ") +
                        (item.fields.length > 2
                          ? ` +${item.fields.length - 2}`
                          : "")
                      : `${item.rule_ids.length} rule finding${item.rule_ids.length === 1 ? "" : "s"}`}
                  </span>
                </td>
                <td>
                  <Badge
                    tone={item.severity === "critical" ? "orange" : "amber"}
                  >
                    <span className="badge-dot" />
                    {humanize(item.severity)}
                  </Badge>
                </td>
                <td>
                  <span
                    className={`status-text ${item.status === "resolved" ? "resolved" : ""}`}
                  >
                    <span />
                    {item.status === "resolved"
                      ? item.resolution?.action === "exclude"
                        ? "Quarantined"
                        : "Resolved"
                      : "Needs review"}
                  </span>
                </td>
                <td>
                  <button
                    className="icon-button"
                    onClick={() => onCase(item.id)}
                    aria-label={`Review ${g?.game_id || item.key}`}
                  >
                    <ChevronRight size={18} />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function Overview({
  run,
  onNavigate,
  onCase,
  onExport,
  exporting,
}: {
  run: RunDetail;
  onNavigate: (view: View) => void;
  onCase: (id: string) => void;
  onExport: () => void;
  exporting: boolean;
}) {
  const manual = Math.max(0, run.published_games - run.auto_matched);
  const segments = [
    { name: "Auto-matched", value: run.auto_matched, color: "#254f40" },
    { name: "Reviewed", value: manual, color: "#82a593" },
    { name: "Needs review", value: run.open_cases, color: "#e59a55" },
    { name: "Quarantined", value: run.excluded_games, color: "#c8cbc4" },
  ];
  const open = run.cases.filter((c) => c.status === "open");
  const topCases = [...open]
    .sort(
      (a, b) =>
        Number(b.severity === "critical") - Number(a.severity === "critical"),
    )
    .slice(0, 4);
  return (
    <>
      <div className="overview-intro">
        <div>
          <div className="eyebrow">Your reconciliation, at a glance</div>
          <h1>
            Every game.
            <br className="mobile-break" /> One trusted record.
          </h1>
          <p>
            Bring scorer and league data into agreement, with evidence for every
            decision.
          </p>
        </div>
        <div className="integrity-mark">
          <ShieldCheck size={24} />
          <div>
            <strong>Evidence, preserved.</strong>
            <span>Rules v{run.rule_version}</span>
          </div>
        </div>
      </div>
      <div className="stats-grid">
        <Stat
          label="Source records"
          value={run.input_rows}
          detail="Across two immutable exports"
          icon={<Database size={18} />}
        />
        <Stat
          label="Publishable games"
          value={run.published_games}
          suffix={`/ ${run.game_count}`}
          detail="Matched or explicitly reviewed"
          icon={<FileCheck2 size={18} />}
        />
        <Stat
          label="Open exceptions"
          value={run.open_cases}
          detail={
            run.open_cases
              ? "A decision is needed before export"
              : "All exceptions have a decision"
          }
          icon={<GitCompareArrows size={18} />}
          accent={!!run.open_cases}
        />
        <Stat
          label="Publishable coverage"
          value={run.quality_score}
          suffix="%"
          detail="Publishable games ÷ all game groups"
          icon={<ShieldCheck size={18} />}
        />
      </div>
      <div className="overview-middle">
        <section className="panel coverage-panel">
          <div className="panel-heading">
            <div>
              <h2>The path to publishable</h2>
              <p>Every unique game group has one outcome.</p>
            </div>
            <Badge>{run.game_count} games</Badge>
          </div>
          <div
            className="coverage-chart"
            role="img"
            aria-label={`Game coverage: ${segments.map((s) => `${s.value} ${s.name.toLowerCase()}`).join(", ")}`}
          >
            {segments
              .filter((s) => s.value > 0)
              .map((s) => (
                <div
                  title={`${s.name}: ${s.value}`}
                  key={s.name}
                  style={{
                    width: `${run.game_count ? (100 * s.value) / run.game_count : 0}%`,
                    background: s.color,
                  }}
                >
                  <span>{s.value}</span>
                </div>
              ))}
          </div>
          <div className="chart-legend">
            {segments.map((s) => (
              <div key={s.name}>
                <span className="legend-dot" style={{ background: s.color }} />
                <span>{s.name}</span>
                <strong>{s.value}</strong>
              </div>
            ))}
          </div>
          <div className="coverage-foot">
            <span>
              <CheckCheck size={16} />
              {run.stats.normalization_count} safe normalization
              {run.stats.normalization_count === 1 ? "" : "s"} applied
            </span>
            <button
              className="text-button"
              onClick={() => onNavigate("lineage")}
            >
              See lineage
              <ArrowUpRight size={14} />
            </button>
          </div>
        </section>
        <section className="panel feeds-panel">
          <div className="panel-heading">
            <div>
              <h2>Two feeds. One game.</h2>
              <p>Joined on season + game ID.</p>
            </div>
            <Layers3 size={19} className="muted" />
          </div>
          {run.sources.map((s) => (
            <div className="feed-row" key={s.id}>
              <span className={`feed-icon ${s.id}`}>
                <FileText size={19} />
              </span>
              <div>
                <strong>{s.label || humanize(s.id)}</strong>
                <span className="mono">SHA-256 {s.sha256.slice(0, 10)}…</span>
              </div>
              <div className="feed-records">
                <strong>{s.row_count}</strong>
                <span>records</span>
              </div>
            </div>
          ))}
          <div className="feed-foot">
            <Fingerprint size={15} />
            Raw evidence is retained for every run.
          </div>
        </section>
      </div>
      <section className="panel queue-preview">
        <div className="panel-heading">
          <div>
            <div className="heading-with-badge">
              <h2>
                {open.length
                  ? "A few things need a second look"
                  : "Ready for the next step"}
              </h2>
              {open.length > 0 && (
                <Badge tone="orange">{open.length} open</Badge>
              )}
            </div>
            <p>
              {open.length
                ? "Review the source evidence, preview the impact, then make the call."
                : "All exceptions have been reviewed. Your export includes its evidence and decision history."}
            </p>
          </div>
          <button className="text-button" onClick={() => onNavigate("queue")}>
            View review queue
            <ArrowRight size={16} />
          </button>
        </div>
        {open.length ? (
          <CasesTable run={run} cases={topCases} onCase={onCase} compact />
        ) : (
          <div className="ready-banner">
            <span className="ready-icon">
              <ClipboardCheck size={31} />
            </span>
            <div>
              <h3>
                {run.excluded_games
                  ? "Review complete. Some games are quarantined."
                  : "The record is reconciled."}
              </h3>
              <p>
                {run.excluded_games
                  ? `${run.excluded_games} excluded game${run.excluded_games === 1 ? "" : "s"} will be declared in the manifest. Publishable coverage remains ${run.quality_score}%.`
                  : "Download canonical data, source files, provenance, and the audit log in one bundle."}
              </p>
            </div>
            <button
              className="button primary"
              disabled={exporting}
              onClick={onExport}
            >
              <ArrowDownToLine size={16} />
              Export bundle
            </button>
          </div>
        )}
      </section>
      <div className="bottom-note">
        <ShieldCheck size={15} />
        <span>
          Deterministic reconciliation. Explicit review. No silent corrections.
        </span>
        <button className="text-button" onClick={() => onNavigate("rules")}>
          How the rules work
          <ArrowUpRight size={13} />
        </button>
      </div>
    </>
  );
}
function Stat({
  label,
  value,
  suffix,
  detail,
  icon,
  accent,
}: {
  label: string;
  value: number;
  suffix?: string;
  detail: string;
  icon: React.ReactNode;
  accent?: boolean;
}) {
  return (
    <div className={`stat-card ${accent ? "stat-attention" : ""}`}>
      <div className="stat-top">
        <span>{label}</span>
        {icon}
      </div>
      <div className="stat-value">
        {value}
        <span>{suffix}</span>
      </div>
      <p>{detail}</p>
    </div>
  );
}
export function Queue({
  run,
  onCase,
}: {
  run: RunDetail;
  onCase: (id: string) => void;
}) {
  const [status, setStatus] = useState("open");
  const [severity, setSeverity] = useState("all");
  const [query, setQuery] = useState("");
  const cases = run.cases.filter(
    (c) =>
      (status === "all" || c.status === status) &&
      (severity === "all" || c.severity === severity) &&
      `${c.key} ${c.title} ${c.kind} ${run.candidates
        .filter((x) => c.candidate_ids.includes(x.id))
        .map((x) => `${x.normalized.home_team} ${x.normalized.away_team}`)
        .join(" ")}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  return (
    <>
      <PageHeading
        eyebrow="Human decisions, preserved"
        title="Review queue"
        description="Compare original records and choose what belongs in the canonical dataset."
      />
      <div className="queue-summary">
        <div>
          <strong>{run.open_cases}</strong>
          <span>Awaiting review</span>
        </div>
        <div>
          <strong>{run.resolved_cases}</strong>
          <span>Decisions recorded</span>
        </div>
        <div>
          <strong>{run.excluded_games}</strong>
          <span>Games quarantined</span>
        </div>
        <p>
          <ShieldCheck size={20} />A reviewed decision applies to the exact
          evidence you saw, never just a matching game ID.
        </p>
      </div>
      <section className="panel">
        <div className="table-toolbar">
          <div className="segmented small-segment" aria-label="Case status">
            {[
              { id: "open", label: "Open" },
              { id: "resolved", label: "Resolved" },
              { id: "all", label: "All cases" },
            ].map((x) => (
              <button
                className={status === x.id ? "active" : ""}
                key={x.id}
                onClick={() => setStatus(x.id)}
              >
                {x.label}
              </button>
            ))}
          </div>
          <div className="filters">
            <SearchField
              value={query}
              onChange={setQuery}
              label="Search review cases"
              placeholder="Search games or teams…"
            />
            <label className="select-wrap">
              <SlidersHorizontal size={15} />
              <select
                aria-label="Filter by severity"
                value={severity}
                onChange={(e) => setSeverity(e.target.value)}
              >
                <option value="all">All severities</option>
                <option value="critical">Critical</option>
                <option value="warning">Warning</option>
              </select>
            </label>
          </div>
        </div>
        <CasesTable run={run} cases={cases} onCase={onCase} />
        <div className="table-footer">
          {cases.length} of {run.cases.length} cases ·{" "}
          {run.stats.exact_duplicates} exact duplicate record
          {run.stats.exact_duplicates === 1 ? "" : "s"} collapsed with
          provenance retained
        </div>
      </section>
    </>
  );
}
export function Canonical({ run }: { run: RunDetail }) {
  const [tab, setTab] = useState<"standings" | "games">("standings");
  const [query, setQuery] = useState("");
  const games = run.canonical.filter((g) =>
    `${g.game_id} ${g.home_team} ${g.away_team}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <>
      <PageHeading
        eyebrow="Built from publishable records"
        title="Canonical data"
        description="One selected result per game. Full source provenance travels with every record."
      />
      <div
        className={`info-callout ${run.open_cases ? "warning-callout" : ""}`}
      >
        <ShieldCheck size={19} />
        <span>
          {run.open_cases
            ? `Provisional dataset: ${run.open_cases} open case${run.open_cases === 1 ? "" : "s"} have not contributed to these results.`
            : run.excluded_games
              ? `Review complete with ${run.excluded_games} quarantined game${run.excluded_games === 1 ? "" : "s"}. This dataset has incomplete coverage.`
              : "Every game group is reconciled. This dataset is ready to export."}
        </span>
      </div>
      <section className="panel">
        <div className="table-toolbar">
          <div className="segmented small-segment">
            <button
              className={tab === "standings" ? "active" : ""}
              onClick={() => setTab("standings")}
            >
              Standings
            </button>
            <button
              className={tab === "games" ? "active" : ""}
              onClick={() => setTab("games")}
            >
              Game records{" "}
              <span className="tab-count">{run.published_games}</span>
            </button>
          </div>
          <SearchField
            value={query}
            onChange={setQuery}
            label="Search canonical data"
            placeholder="Search teams or games…"
          />
        </div>
        {tab === "standings" ? (
          <div className="table-scroll">
            <table className="data-table standings-table">
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Team</th>
                  <th title="Games played">GP</th>
                  <th>W</th>
                  <th>L</th>
                  <th>T</th>
                  <th title="Goals for">GF</th>
                  <th title="Goals against">GA</th>
                  <th title="Goal difference">DIFF</th>
                  <th>PTS</th>
                </tr>
              </thead>
              <tbody>
                {run.standings
                  .map((s, i) => ({ ...s, rank: i + 1 }))
                  .filter((s) =>
                    s.team.toLowerCase().includes(query.toLowerCase()),
                  )
                  .map((s) => (
                    <tr key={s.team}>
                      <td>
                        <span
                          className={`rank ${s.rank === 1 ? "rank-first" : ""}`}
                        >
                          {s.rank.toString().padStart(2, "0")}
                        </span>
                      </td>
                      <td className="team-name">
                        <span className="team-avatar">
                          {s.team
                            .split(" ")
                            .map((x) => x[0])
                            .slice(0, 2)
                            .join("")}
                        </span>
                        <strong>{s.team}</strong>
                      </td>
                      <td>{s.played}</td>
                      <td>{s.wins}</td>
                      <td>{s.losses}</td>
                      <td>{s.ties}</td>
                      <td>{s.goals_for}</td>
                      <td>{s.goals_against}</td>
                      <td className={s.goal_difference >= 0 ? "positive" : ""}>
                        {s.goal_difference > 0 ? "+" : ""}
                        {s.goal_difference}
                      </td>
                      <td>
                        <strong className="points">{s.points}</strong>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {!run.standings.length && (
              <Empty title="No final games to rank">
                Standings appear when valid final game records become
                publishable.
              </Empty>
            )}
          </div>
        ) : (
          <div className="table-scroll">
            <table className="data-table games-table">
              <thead>
                <tr>
                  <th>Game</th>
                  <th>Away</th>
                  <th>Score</th>
                  <th>Home</th>
                  <th>Shots (A–H)</th>
                  <th>Status</th>
                  <th>Source rows</th>
                </tr>
              </thead>
              <tbody>
                {games.map((g) => (
                  <tr key={g.key}>
                    <td>
                      <strong className="mono">{g.game_id}</strong>
                      <span className="table-subtext">{g.game_date}</span>
                    </td>
                    <td>{g.away_team}</td>
                    <td className="score">
                      {pretty(g.away_goals)} : {pretty(g.home_goals)}
                    </td>
                    <td>{g.home_team}</td>
                    <td className="mono">
                      {pretty(g.away_shots)} – {pretty(g.home_shots)}
                    </td>
                    <td>
                      <Badge tone={g.status === "final" ? "green" : "neutral"}>
                        {humanize(g.status)}
                      </Badge>
                    </td>
                    <td>
                      <code className="provenance">
                        {g.provenance.join(", ")}
                      </code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!games.length && (
              <Empty title="No matching records">
                Try another team or game ID.
              </Empty>
            )}
          </div>
        )}
        <div className="table-footer">
          Demo standings policy: final games only · Win 2 pts · Tie 1 pt · Loss
          0 pts · Tiebreak: goal difference, goals for, team name
        </div>
      </section>
    </>
  );
}
export function Lineage({ run }: { run: RunDetail }) {
  const [copied, setCopied] = useState("");
  const [query, setQuery] = useState("");
  const copy = async (sha: string) => {
    try {
      await navigator.clipboard.writeText(sha);
      setCopied(sha);
    } catch {
      setCopied("");
    }
  };
  const normalizations = run.normalizations.filter((n) =>
    `${n.field} ${pretty(n.before)} ${pretty(n.after)} ${n.reason}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <>
      <PageHeading
        eyebrow="From source to canonical"
        title="Source lineage"
        description="Follow the bytes, transformations, and evidence behind each reconciliation."
      />
      <div className="lineage-flow">
        <div>
          <Database size={22} />
          <strong>Original CSVs</strong>
          <span>{run.input_rows} records</span>
        </div>
        <ArrowRight size={21} />
        <div>
          <SlidersHorizontal size={22} />
          <strong>Safe normalization</strong>
          <span>{run.stats.normalization_count} transformations</span>
        </div>
        <ArrowRight size={21} />
        <div>
          <GitCompareArrows size={22} />
          <strong>Exact-key reconciliation</strong>
          <span>{run.game_count} game groups</span>
        </div>
        <ArrowRight size={21} />
        <div>
          <FileCheck2 size={22} />
          <strong>Reviewed canonical data</strong>
          <span>{run.published_games} publishable games</span>
        </div>
      </div>
      <div className="source-cards">
        {run.sources.map((s) => (
          <section className="panel source-card" key={s.id}>
            <div className="panel-heading">
              <div>
                <span className={`feed-icon ${s.id}`}>
                  <FileText size={19} />
                </span>
                <h2>{s.label || humanize(s.id)}</h2>
              </div>
              <Badge>{s.row_count} rows</Badge>
            </div>
            <span className="eyebrow">Original bytes · SHA-256</span>
            <div className="hash-row">
              <code>{s.sha256}</code>
              <button
                className="icon-button"
                aria-label={`Copy ${s.id} checksum`}
                onClick={() => void copy(s.sha256)}
              >
                {copied === s.sha256 ? <Check size={17} /> : <Copy size={17} />}
              </button>
            </div>
            <p className="small muted">
              Source contents and row numbers remain unchanged after review. Raw
              CSVs are included in the export bundle.
            </p>
          </section>
        ))}
      </div>
      <section className="panel">
        <div className="table-toolbar">
          <div>
            <h2>Normalization ledger</h2>
            <p className="small muted">
              Only explicit, deterministic transformations. No fuzzy matching.
            </p>
          </div>
          <SearchField
            value={query}
            onChange={setQuery}
            label="Search normalizations"
            placeholder="Search transformations…"
          />
        </div>
        {normalizations.length ? (
          <div className="table-scroll">
            <table className="data-table normalization-table">
              <thead>
                <tr>
                  <th>Source / row</th>
                  <th>Field</th>
                  <th>Before</th>
                  <th>After</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {normalizations.map((n, i) => (
                  <tr key={`${n.source}-${n.row_number}-${n.field}-${i}`}>
                    <td>
                      <strong>{humanize(n.source || "")}</strong>
                      <span className="table-subtext">row {n.row_number}</span>
                    </td>
                    <td>{humanize(n.field)}</td>
                    <td>
                      <code>{JSON.stringify(n.before)}</code>
                    </td>
                    <td>
                      <code>{JSON.stringify(n.after)}</code>
                    </td>
                    <td>{n.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="No transformations in this view">
            Source values already matched the canonical format, or no
            transformations match your search.
          </Empty>
        )}
        <div className="table-footer">
          Rules v{run.rule_version} · Exact duplicate rows retain all source
          references.
        </div>
      </section>
    </>
  );
}
export function Audit({ run }: { run: RunDetail }) {
  return (
    <>
      <PageHeading
        eyebrow="Decisions with a paper trail"
        title="Audit trail"
        description="Inspect who recorded each decision, why they made it, and which evidence it used."
      />
      <div className="audit-banner">
        <Fingerprint size={25} />
        <div>
          <strong>Linked, append-only event history</strong>
          <p>
            Each event includes the previous event’s hash. Reviewer names are
            self-reported. This is a local integrity check, not authentication
            or protection from a database administrator.
          </p>
        </div>
        <Badge>{run.audit.length} events</Badge>
      </div>
      <section className="panel audit-panel">
        {run.audit.length ? (
          [...run.audit].reverse().map((e, i) => (
            <article className="audit-event" key={e.id}>
              <div className="audit-rail">
                <span className={i === 0 ? "latest" : ""}>
                  {e.action.includes("replay") ? (
                    <Copy size={17} />
                  ) : e.action.includes("select") ||
                    e.action.includes("resolv") ? (
                    <ClipboardCheck size={17} />
                  ) : (
                    <Database size={17} />
                  )}
                </span>
              </div>
              <div className="audit-content">
                <div className="audit-event-top">
                  <div>
                    <h3>{humanize(e.action)}</h3>
                    <Badge>{e.case_id ? "Case decision" : "Run event"}</Badge>
                  </div>
                  <time dateTime={e.created_at}>{dateTime(e.created_at)}</time>
                </div>
                <p>
                  {e.note || "Event recorded by the reconciliation service."}
                </p>
                <div className="audit-meta">
                  <span>
                    Reviewer label: <strong>{e.reviewer || "System"}</strong>
                  </span>
                  {e.case_id && <code>{e.case_id}</code>}
                </div>
                <details className="event-details">
                  <summary>Inspect event evidence</summary>
                  <dl>
                    <dt>Event ID</dt>
                    <dd>{e.id}</dd>
                    <dt>Previous hash</dt>
                    <dd>{e.previous_hash || "Genesis event"}</dd>
                    <dt>Event hash</dt>
                    <dd>{e.event_hash}</dd>
                  </dl>
                  <pre>{JSON.stringify(e.details, null, 2)}</pre>
                </details>
              </div>
            </article>
          ))
        ) : (
          <Empty title="No events recorded">
            New review decisions will appear here.
          </Empty>
        )}
      </section>
    </>
  );
}
export function Rulebook({ run }: { run: RunDetail }) {
  return (
    <>
      <PageHeading
        eyebrow={`Deterministic engine · version ${run.rule_version}`}
        title="The rulebook"
        description="Predictable checks, clear exceptions, and no guessing about which data is correct."
      />
      <div className="rule-principles">
        <div>
          <span>01</span>
          <h3>Match identities exactly.</h3>
          <p>
            Season + game ID. Similar team names or scores never create a join.
          </p>
        </div>
        <div>
          <span>02</span>
          <h3>Keep the original evidence.</h3>
          <p>Invalid rows become review cases. Raw values remain available.</p>
        </div>
        <div>
          <span>03</span>
          <h3>Make uncertainty explicit.</h3>
          <p>
            Conflicts need a reviewed choice. Exclusions stay visible in the
            manifest.
          </p>
        </div>
      </div>
      <div className="rules-grid">
        {run.rules.map((rule) => (
          <section className="panel rule-card" key={rule.id}>
            <div>
              <code>{rule.id}</code>
              <Badge
                tone={
                  rule.severity === "critical"
                    ? "orange"
                    : rule.severity === "warning"
                      ? "amber"
                      : "green"
                }
              >
                {humanize(rule.severity)}
              </Badge>
            </div>
            <h3>{rule.title}</h3>
            <p>{rule.description}</p>
          </section>
        ))}
      </div>
      <section className="panel method-panel">
        <h2>What this workbench does not assume</h2>
        <div>
          <p>
            <strong>No automatic winner.</strong> A later timestamp does not
            make a conflicting result correct. The reviewer chooses a valid
            record.
          </p>
          <p>
            <strong>No missing-value guesses.</strong> Scheduled games may omit
            stats. Final games require valid scores and shots. Blank values
            never silently become zero.
          </p>
          <p>
            <strong>No blind decision reuse.</strong> Replay requires the same
            rule version and a fingerprint of all candidate evidence. Changed
            content requires a new review.
          </p>
          <p>
            <strong>No production identity layer.</strong> Reviewer labels are
            self-reported. This local demo is designed for inspection and
            reproducibility.
          </p>
        </div>
      </section>
    </>
  );
}
export function PageHeading({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <div className="page-heading">
      <div className="eyebrow">{eyebrow}</div>
      <h1>{title}</h1>
      <p>{description}</p>
    </div>
  );
}
function SearchField({
  value,
  onChange,
  label,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  placeholder: string;
}) {
  return (
    <label className="search-field">
      <Search size={16} />
      <input
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </label>
  );
}
