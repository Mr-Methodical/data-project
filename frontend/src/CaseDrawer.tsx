import { useState } from "react";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  Eye,
  FileCheck2,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import { api, ApiError } from "./api";
import type { Case, Candidate, Preview, RunDetail } from "./types";
import { Badge, ErrorBox, Modal, Spinner, humanize, pretty } from "./ui";
const fields = [
  "season",
  "game_id",
  "game_date",
  "home_team",
  "away_team",
  "home_goals",
  "away_goals",
  "home_shots",
  "away_shots",
  "status",
  "venue",
  "updated_at",
];
export default function CaseDrawer({
  run,
  item,
  reviewer,
  setReviewer,
  onClose,
  onUpdated,
}: {
  run: RunDetail;
  item: Case;
  reviewer: string;
  setReviewer: (value: string) => void;
  onClose: () => void;
  onUpdated: (run: RunDetail) => void;
}) {
  const candidates = item.candidate_ids
    .map((id) => run.candidates.find((c) => c.id === id))
    .filter((c): c is Candidate => !!c);
  const [choice, setChoice] = useState(
    item.resolution?.action === "exclude"
      ? "__exclude__"
      : item.resolution?.candidate_id || "",
  );
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const excluded = choice === "__exclude__";
  const resolved = item.status === "resolved";
  const select = (value: string) => {
    setChoice(value);
    setPreview(null);
    setError("");
  };
  const prepare = async () => {
    setBusy("preview");
    setError("");
    try {
      setPreview(
        await api.preview(
          run.id,
          item.id,
          excluded ? "exclude" : "select",
          excluded ? undefined : choice,
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Preview failed.");
    } finally {
      setBusy("");
    }
  };
  const apply = async () => {
    if (!preview) return;
    setBusy("resolve");
    setError("");
    try {
      const next = await api.resolve(run.id, item.id, {
        action: excluded ? "exclude" : "select",
        candidate_id: excluded ? undefined : choice,
        expected_version: item.version,
        note: note.trim(),
        reviewer: reviewer.trim(),
      });
      onUpdated(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Decision could not be saved.");
      if (e instanceof ApiError && e.status === 409) setPreview(null);
    } finally {
      setBusy("");
    }
  };
  const beforeTeams = new Map(
    preview?.before.standings.map((t) => [t.team, t]) || [],
  );
  const changedTeams =
    preview?.after.standings.filter((t) => {
      const b = beforeTeams.get(t.team);
      return (
        !b ||
        b.points !== t.points ||
        b.played !== t.played ||
        b.goals_for !== t.goals_for ||
        b.goals_against !== t.goals_against
      );
    }) || [];
  return (
    <Modal
      title={item.title}
      subtitle={`Review case · ${item.key}`}
      drawer
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <div className="drawer-body">
        <div className="case-heading-meta">
          <Badge
            tone={
              resolved
                ? "green"
                : item.severity === "critical"
                  ? "orange"
                  : "amber"
            }
          >
            {resolved
              ? item.resolution?.action === "exclude"
                ? "Quarantined"
                : "Resolved"
              : item.severity}
          </Badge>
          <Badge>{humanize(item.kind)}</Badge>
          <span className="small muted">
            {candidates.length} source records
          </span>
        </div>
        <p className="case-description">{item.description}</p>
        <div className="rule-chips">
          {item.rule_ids.map((id) => (
            <code key={id}>{id}</code>
          ))}
        </div>
        <section className="drawer-section">
          <div className="section-heading">
            <div>
              <span className="step-number">01</span>
              <h3>Inspect source evidence</h3>
            </div>
            <span className="small muted">Raw data stays intact</span>
          </div>
          <p className="small muted">
            Highlighted rows need attention. Each value is shown exactly as
            supplied and after deterministic normalization.
          </p>
          {item.fields.length > 0 && (
            <div className="comparison-wrap">
              <table className="comparison-table">
                <caption>Values that need attention</caption>
                <thead>
                  <tr>
                    <th>Field</th>
                    {candidates.map((c) => (
                      <th key={c.id}>
                        {humanize(c.source)}
                        <span>row {c.row_number}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {item.fields.map((field) => (
                    <tr key={field}>
                      <th>{humanize(field)}</th>
                      {candidates.map((c) => (
                        <td key={c.id}>
                          {pretty(
                            (
                              c.normalized as unknown as Record<string, unknown>
                            )[field],
                          )}
                          {c.errors.some((e) => e.field === field) && (
                            <span className="comparison-invalid">Invalid</span>
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {candidates.map((c) => (
            <details className="evidence-card" key={c.id}>
              <summary>
                <span className={`source-dot ${c.source}`} />
                <strong>{humanize(c.source)}</strong>
                <span className="mono small">row {c.row_number}</span>
                <Badge tone={c.valid ? "green" : "orange"}>
                  {c.valid ? "Valid candidate" : "Invalid record"}
                </Badge>
                <ChevronDown size={16} />
              </summary>
              {c.errors.length > 0 && (
                <div className="validation-list">
                  {c.errors.map((e, i) => (
                    <p key={`${e.rule_id}-${i}`}>
                      <ShieldAlert size={14} />
                      <span>
                        <strong>{humanize(e.field)}:</strong> {e.message}
                      </span>
                    </p>
                  ))}
                </div>
              )}
              <div className="evidence-table-wrap">
                <table className="evidence-table">
                  <thead>
                    <tr>
                      <th>Field</th>
                      <th>Raw value</th>
                      <th>Normalized</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Array.from(
                      new Set([...fields, ...Object.keys(c.raw)]),
                    ).map((field) => {
                      const normalized = (
                        c.normalized as unknown as Record<string, unknown>
                      )[field];
                      const normalization = c.normalizations.find(
                        (n) => n.field === field,
                      );
                      const extra = !fields.includes(field);
                      return (
                        <tr
                          key={field}
                          className={
                            item.fields.includes(field) ||
                            c.errors.some((e) => e.field === field)
                              ? "field-conflict"
                              : ""
                          }
                        >
                          <th>
                            {humanize(field)}
                            {extra && <small>extra column</small>}
                          </th>
                          <td>
                            <code>{JSON.stringify(c.raw[field]) ?? "—"}</code>
                          </td>
                          <td>
                            {extra ? (
                              <span className="muted">Not exported</span>
                            ) : (
                              <>
                                <span>{pretty(normalized)}</span>
                                {normalization && (
                                  <small className="normalization-note">
                                    {normalization.reason}
                                  </small>
                                )}
                              </>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </details>
          ))}
        </section>
        {resolved && item.resolution ? (
          <div className="resolved-panel">
            <CheckCircle2 size={22} />
            <div>
              <h3>
                {item.resolution.action === "exclude"
                  ? "Game quarantined from export"
                  : "Decision saved"}
              </h3>
              <p>{item.resolution.note}</p>
              <span className="small muted">
                Reviewer label: {item.resolution.reviewer}
                {item.resolution.replayed_from
                  ? " · Reused from matching evidence"
                  : ""}
              </span>
            </div>
          </div>
        ) : (
          <>
            <section className="drawer-section">
              <div className="section-heading">
                <div>
                  <span className="step-number">02</span>
                  <h3>Choose the canonical record</h3>
                </div>
              </div>
              <p className="small muted">
                Select a complete, valid source record or quarantine the entire
                game. No fields are silently blended.
              </p>
              <fieldset className="choice-list">
                <legend className="sr-only">Decision</legend>
                {candidates.map((c) => (
                  <label
                    className={`decision-choice ${choice === c.id ? "selected" : ""} ${!c.valid ? "disabled" : ""}`}
                    key={c.id}
                  >
                    <input
                      data-testid={`candidate-${c.id}`}
                      type="radio"
                      name="candidate"
                      value={c.id}
                      checked={choice === c.id}
                      disabled={!c.valid || !!busy}
                      onChange={() => select(c.id)}
                    />
                    <div>
                      <strong>
                        Use {humanize(c.source)} · row {c.row_number}
                      </strong>
                      <span>
                        {c.valid
                          ? `${pretty(c.normalized.away_team)} ${pretty(c.normalized.away_goals)} — ${pretty(c.normalized.home_goals)} ${pretty(c.normalized.home_team)}`
                          : "Invalid records cannot be published."}
                      </span>
                    </div>
                    {choice === c.id && <Check size={17} />}
                  </label>
                ))}
                <label
                  className={`decision-choice quarantine-choice ${excluded ? "selected" : ""}`}
                >
                  <input
                    type="radio"
                    name="candidate"
                    value="__exclude__"
                    checked={excluded}
                    onChange={() => select("__exclude__")}
                    disabled={!!busy}
                  />
                  <div>
                    <strong>Quarantine this game</strong>
                    <span>
                      Exclude it from canonical data and mark coverage
                      incomplete.
                    </span>
                  </div>
                  <XCircle size={18} />
                </label>
              </fieldset>
              <button
                className="button full-width"
                data-testid="preview-button"
                onClick={() => void prepare()}
                disabled={!choice || !!busy}
              >
                {busy === "preview" ? (
                  <Spinner label="Calculating preview…" />
                ) : (
                  <>
                    <Eye size={17} />
                    Preview impact
                  </>
                )}
              </button>
            </section>
            {preview && (
              <section className="preview-panel" aria-label="Decision preview">
                <div className="section-heading">
                  <div>
                    <Eye size={17} />
                    <h3>Impact preview</h3>
                  </div>
                  <Badge tone={excluded ? "amber" : "green"}>
                    {excluded ? "Exclude game" : "Publish record"}
                  </Badge>
                </div>
                <div className="preview-count">
                  <span>Publishable games</span>
                  <strong>
                    {preview.before.published_games}
                    <ArrowRight size={17} />
                    {preview.after.published_games}
                  </strong>
                </div>
                {preview.changes.length > 0 && (
                  <>
                    <p className="small muted">
                      Changes from the first valid scorer record (or first valid
                      source).
                    </p>
                    <table className="compact-table">
                      <thead>
                        <tr>
                          <th>Field</th>
                          <th>Baseline</th>
                          <th>Selected</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.changes.map((c, i) => (
                          <tr key={`${c.field}-${i}`}>
                            <th>{humanize(c.field)}</th>
                            <td>{pretty(c.before)}</td>
                            <td>{pretty(c.after)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                )}
                {changedTeams.length > 0 && (
                  <>
                    <h4>Standings impact</h4>
                    <table className="compact-table">
                      <thead>
                        <tr>
                          <th>Team</th>
                          <th>Played</th>
                          <th>Points</th>
                        </tr>
                      </thead>
                      <tbody>
                        {changedTeams.map((t) => (
                          <tr key={t.team}>
                            <th>{t.team}</th>
                            <td>
                              {beforeTeams.get(t.team)?.played || 0} →{" "}
                              {t.played}
                            </td>
                            <td>
                              {beforeTeams.get(t.team)?.points || 0} →{" "}
                              {t.points}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                )}
                <p className="small">
                  {excluded
                    ? "The excluded key will be listed in the export manifest. Coverage will remain below 100%."
                    : preview.selected?.status === "scheduled"
                      ? "Scheduled games do not contribute to standings."
                      : "Standings use final, publishable games only."}
                </p>
              </section>
            )}
            <section className="drawer-section">
              <div className="section-heading">
                <div>
                  <span className="step-number">03</span>
                  <h3>Record your decision</h3>
                </div>
              </div>
              <label className="field-label" htmlFor="reviewer">
                Reviewer name
              </label>
              <input
                id="reviewer"
                data-testid="reviewer-name"
                value={reviewer}
                onChange={(e) => setReviewer(e.target.value)}
                placeholder="Your name"
                maxLength={80}
              />
              <p className="field-help">
                A self-reported label for this local workbench, not an
                authenticated identity.
              </p>
              <label className="field-label" htmlFor="decision-note">
                Decision reason
              </label>
              <textarea
                id="decision-note"
                data-testid="decision-note"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                maxLength={1000}
                rows={3}
                placeholder="What evidence supports this choice?"
              />
              <span className="field-help">
                8–1,000 characters. Saved with the evidence fingerprint.
              </span>
            </section>
            {error && <ErrorBox>{error}</ErrorBox>}
          </>
        )}
        <details className="fingerprint">
          <summary>Evidence fingerprint</summary>
          <code>{item.fingerprint}</code>
          <p className="small muted">
            Decision reuse requires this exact evidence fingerprint and the same
            rule version.
          </p>
        </details>
      </div>
      <div className="drawer-footer">
        {resolved ? (
          <>
            <span className="small muted">
              Accepted decisions are immutable.
            </span>
            <button className="button primary" onClick={onClose}>
              Done
            </button>
          </>
        ) : (
          <>
            <div className="small muted">
              {!preview
                ? "Preview the impact before applying."
                : "Your decision is saved to the audit trail."}
            </div>
            <button
              className="button primary"
              disabled={
                !preview || !reviewer.trim() || note.trim().length < 8 || !!busy
              }
              data-testid="apply-decision"
              onClick={() => void apply()}
            >
              {busy === "resolve" ? (
                <Spinner label="Saving…" />
              ) : (
                <>
                  <FileCheck2 size={17} />
                  Apply decision
                </>
              )}
            </button>
          </>
        )}
      </div>
    </Modal>
  );
}
