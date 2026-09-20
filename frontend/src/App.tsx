import { useEffect, useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronDown,
  Database,
  ExternalLink,
  Files,
  FlaskConical,
  GitCompareArrows,
  History,
  LayoutDashboard,
  Menu,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  X,
} from "lucide-react";
import { api } from "./api";
import type { RunDetail, RunSummary, View } from "./types";
import { Audit, Canonical, Lineage, Overview, Queue, Rulebook } from "./Views";
import CaseDrawer from "./CaseDrawer";
import ImportDialog from "./ImportDialog";
import { Badge, Empty, ErrorBox, Modal, Spinner } from "./ui";
const navItems = [
  { id: "overview", name: "Overview", icon: LayoutDashboard },
  { id: "queue", name: "Review queue", icon: GitCompareArrows },
  { id: "canonical", name: "Canonical data", icon: Database },
  { id: "lineage", name: "Source lineage", icon: Files },
  { id: "audit", name: "Audit trail", icon: History },
  { id: "rules", name: "Rulebook", icon: BookOpen },
] as const;
const storageGet = (key: string) => {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
};
export default function App() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [view, setView] = useState<View>("overview");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [replayOpen, setReplayOpen] = useState(false);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [sidebar, setSidebar] = useState(false);
  const [reviewer, setReviewer] = useState(() =>
    storageGet("rinkcheck.reviewer"),
  );
  useEffect(() => {
    try {
      localStorage.setItem("rinkcheck.reviewer", reviewer);
    } catch {
      /* optional persistence */
    }
  }, [reviewer]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 6000);
    return () => clearTimeout(t);
  }, [toast]);
  const accept = (next: RunDetail) => {
    setRun(next);
    setRuns((prev) =>
      [next, ...prev.filter((x) => x.id !== next.id)].sort((a, b) =>
        b.created_at.localeCompare(a.created_at),
      ),
    );
    setError("");
    try {
      localStorage.setItem("rinkcheck.run", next.id);
    } catch {
      /* optional persistence */
    }
  };
  const init = async () => {
    setLoading(true);
    setError("");
    try {
      const list = await api.runs();
      setRuns(list.runs);
      const last = storageGet("rinkcheck.run");
      const id = list.runs.find((x) => x.id === last)?.id || list.runs[0]?.id;
      if (id) accept(await api.run(id));
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Could not connect to the local workbench.",
      );
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void init();
  }, []); // Initial read only; accepted mutations update state immediately.
  const selectRun = async (id: string) => {
    setBusy("run");
    setCaseId(null);
    setError("");
    try {
      accept(await api.run(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load run.");
    } finally {
      setBusy("");
    }
  };
  const navigate = (v: View) => {
    setView(v);
    setSidebar(false);
    setCaseId(null);
    window.scrollTo({ top: 0, behavior: "instant" });
  };
  const loadFollowup = async () => {
    setBusy("followup");
    setError("");
    try {
      accept(await api.demo("followup"));
      setToast(
        "Follow-up release loaded. Reuse matching decisions to carry reviews forward.",
      );
      setView("queue");
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Could not load follow-up release.",
      );
    } finally {
      setBusy("");
    }
  };
  const replay = async () => {
    if (!run) return;
    setBusy("replay");
    setError("");
    try {
      const result = await api.replay(run.id, reviewer.trim());
      accept(result.run);
      setReplayOpen(false);
      setToast(
        `${result.applied} decision${result.applied === 1 ? "" : "s"} reused. Changed evidence remains open for review.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reuse decisions.");
    } finally {
      setBusy("");
    }
  };
  const exportRun = async () => {
    if (!run) return;
    setBusy("export");
    setError("");
    try {
      const response = await fetch(api.exportUrl(run.id));
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(
          typeof payload.detail === "string"
            ? payload.detail
            : "Export failed.",
        );
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `rinkcheck-${run.id}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setToast(
        "Export downloaded: canonical data, manifest, audit, and both original sources.",
      );
      accept(await api.run(run.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not export run.");
    } finally {
      setBusy("");
    }
  };
  const selectedCase = run?.cases.find((c) => c.id === caseId);
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      {sidebar && (
        <button
          className="sidebar-scrim"
          onClick={() => setSidebar(false)}
          aria-label="Close navigation"
        />
      )}
      <aside
        className={`sidebar ${sidebar ? "sidebar-open" : ""}`}
        aria-label="Main navigation"
      >
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            navigate("overview");
          }}
        >
          <RinkLogo />
          <span>
            RinkCheck
            <span className="brand-subtitle">THE DATA INTEGRITY WORKBENCH</span>
          </span>
        </a>
        <div className="workspace-label">
          <span className="workspace-avatar">RC</span>
          <div>
            <strong>Hockey operations</strong>
            <span>Local workspace</span>
          </div>
          <ChevronDown size={15} />
        </div>
        <div className="nav-caption">WORKSPACE</div>
        <nav>
          {navItems.map((item) => (
            <button
              key={item.id}
              className={`nav-link ${view === item.id ? "active" : ""}`}
              aria-current={view === item.id ? "page" : undefined}
              onClick={() => navigate(item.id)}
            >
              <item.icon size={18} />
              <span>{item.name}</span>
              {item.id === "queue" && !!run?.open_cases && (
                <span className="nav-count">{run.open_cases}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-demo">
            <span className="mini-rink">
              <RinkLogo />
            </span>
            <span className="eyebrow">BUILT FOR THE DETAILS</span>
            <h3>
              A better record
              <br />
              of the game.
            </h3>
            <p>
              Catch the differences.
              <br />
              Keep the evidence.
            </p>
            <button onClick={() => setImportOpen(true)}>
              Explore sample data
              <ArrowUpRightIcon />
            </button>
          </div>
          <div className="sidebar-footer">
            <ShieldCheck size={15} />
            <span>Local-first · No cloud required</span>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              onClick={() => setSidebar(true)}
              aria-label="Open navigation"
            >
              <Menu size={22} />
            </button>
            <span>Workspace</span>
            <span className="breadcrumb-slash">/</span>
            <strong>{navItems.find((x) => x.id === view)?.name}</strong>
          </div>
          <div className="topbar-right">
            <span className="local-mode">
              <span />
              Local review workspace
            </span>
            <span
              className="avatar"
              title="Reviewer identities are self-reported"
            >
              RC
            </span>
          </div>
        </header>
        <main id="main-content" className="main-content">
          <div className="run-toolbar">
            <div className="run-context">
              <label htmlFor="run-selector">RECONCILIATION RUN</label>
              <div className="run-selector-wrap">
                <select
                  id="run-selector"
                  data-testid="run-selector"
                  value={run?.id || ""}
                  disabled={loading || !!busy || !runs.length}
                  onChange={(e) => void selectRun(e.target.value)}
                >
                  {!runs.length && <option value="">No runs yet</option>}
                  {runs.map((r) => (
                    <option value={r.id} key={r.id}>
                      {r.name}
                    </option>
                  ))}
                </select>
                <ChevronDown size={15} />
              </div>
              {run?.is_demo && (
                <Badge tone="amber">
                  <FlaskConical size={12} />
                  Synthetic demo
                </Badge>
              )}
            </div>
            <div className="run-actions">
              <button
                className="icon-button outlined refresh-button"
                title="Refresh current run"
                aria-label="Refresh current run"
                disabled={!!busy || loading}
                onClick={() => (run ? void selectRun(run.id) : void init())}
              >
                <RefreshCw size={16} className={busy === "run" ? "spin" : ""} />
              </button>
              <button
                data-testid="export-button"
                className="button"
                disabled={!run || !!run.open_cases || !!busy}
                title={
                  run?.open_cases
                    ? `Resolve ${run.open_cases} open cases before exporting`
                    : "Download canonical CSV, raw sources, manifest, and audit"
                }
                onClick={() => void exportRun()}
              >
                {busy === "export" ? (
                  <Spinner label="Exporting…" />
                ) : (
                  <>
                    <ArrowDownToLine size={16} />
                    Export bundle
                  </>
                )}
              </button>
              <button
                className="button primary"
                data-testid="import-button"
                onClick={() => setImportOpen(true)}
                disabled={!!busy}
              >
                <Plus size={17} />
                New run
              </button>
            </div>
          </div>
          {error && (
            <div className="page-error">
              <ErrorBox>{error}</ErrorBox>
              <button
                className="text-button"
                onClick={() => setError("")}
                aria-label="Dismiss error"
              >
                <X size={17} />
              </button>
            </div>
          )}
          {loading ? (
            <div className="page-loading">
              <Spinner label="Loading your workbench…" />
            </div>
          ) : !run ? (
            <section className="panel initial-empty">
              <Empty title="Bring your first pair of exports">
                <span>
                  Upload scorer and league files, or explore a synthetic demo
                  with deliberate data-quality issues.
                </span>
              </Empty>
              <button
                className="button primary"
                onClick={() => setImportOpen(true)}
              >
                <Plus size={17} />
                Start a reconciliation
              </button>
              {error && (
                <button className="text-button" onClick={() => void init()}>
                  Retry connection
                </button>
              )}
            </section>
          ) : (
            <>
              {(run.replay_available > 0 || run.is_demo) && (
                <div className="release-strip">
                  <span>
                    <RotateCcw size={16} />
                    {run.replay_available > 0 ? (
                      <>
                        <strong>
                          {run.replay_available} previous decision
                          {run.replay_available === 1 ? "" : "s"}
                        </strong>{" "}
                        match this run’s exact evidence.
                      </>
                    ) : (
                      <>
                        Review once. Reuse only when{" "}
                        <strong>all the evidence matches.</strong>
                      </>
                    )}
                  </span>
                  <div>
                    <button
                      data-testid="replay-button"
                      className="text-button"
                      disabled={!!busy || !run.replay_available}
                      onClick={() => setReplayOpen(true)}
                    >
                      Reuse decisions
                      {run.replay_available > 0 && (
                        <span className="inline-count">
                          {run.replay_available}
                        </span>
                      )}
                    </button>
                    {run.is_demo && (
                      <button
                        className="text-button followup-button"
                        disabled={!!busy}
                        onClick={() => void loadFollowup()}
                      >
                        {busy === "followup" ? (
                          <Spinner label="Loading…" />
                        ) : (
                          <>
                            Load follow-up demo
                            <ArrowRight size={14} />
                          </>
                        )}
                      </button>
                    )}
                  </div>
                </div>
              )}
              <div
                className={
                  busy === "run"
                    ? "view-container refreshing"
                    : "view-container"
                }
                aria-busy={busy === "run"}
              >
                {view === "overview" && (
                  <Overview
                    run={run}
                    onNavigate={navigate}
                    onCase={setCaseId}
                    onExport={() => void exportRun()}
                    exporting={!!busy}
                  />
                )}
                {view === "queue" && <Queue run={run} onCase={setCaseId} />}
                {view === "canonical" && <Canonical run={run} />}
                {view === "lineage" && <Lineage run={run} />}
                {view === "audit" && <Audit run={run} />}
                {view === "rules" && <Rulebook run={run} />}
              </div>
              <footer className="page-footer">
                <span>
                  RinkCheck <span className="footer-dot">·</span> Evidence
                  before assumptions.
                </span>
                <span>
                  {run.is_demo
                    ? "Fictional teams. Synthetic data. Real reconciliation."
                    : "Uploaded source data · Local review workflow"}
                </span>
              </footer>
            </>
          )}
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={20} />
          <span>{toast}</span>
          <button
            className="icon-button"
            onClick={() => setToast("")}
            aria-label="Dismiss notification"
          >
            <X size={17} />
          </button>
        </div>
      )}
      {importOpen && (
        <ImportDialog
          onClose={() => setImportOpen(false)}
          onCreated={(next) => {
            accept(next);
            setImportOpen(false);
            setView("overview");
            setCaseId(null);
            setToast(
              "Reconciliation loaded. Source evidence has been preserved.",
            );
          }}
        />
      )}
      {run && selectedCase && (
        <CaseDrawer
          key={`${run.id}-${selectedCase.id}-${selectedCase.version}`}
          run={run}
          item={selectedCase}
          reviewer={reviewer}
          setReviewer={setReviewer}
          onClose={() => setCaseId(null)}
          onUpdated={(next) => {
            accept(next);
            setCaseId(null);
            setToast(
              "Decision saved. Canonical data and standings have been recalculated.",
            );
          }}
        />
      )}
      {run && replayOpen && (
        <Modal
          title="Reuse reviewed decisions"
          subtitle="Exact evidence match"
          onClose={() => {
            if (!busy) setReplayOpen(false);
          }}
        >
          <div className="modal-body">
            <div className="replay-explanation">
              <span>
                <RotateCcw size={27} />
              </span>
              <h3>{run.replay_available} decisions can carry forward</h3>
              <p>
                Only prior human-reviewed choices with an identical evidence
                fingerprint and rule version are eligible. Changed evidence
                stays open. Each reused decision links to its original audit
                event.
              </p>
            </div>
            <label className="field-label" htmlFor="replay-reviewer">
              Reviewer name
            </label>
            <input
              id="replay-reviewer"
              value={reviewer}
              maxLength={80}
              onChange={(e) => setReviewer(e.target.value)}
              placeholder="Your name"
            />
            <p className="field-help">
              Self-reported label for this replay action.
            </p>
            {error && <ErrorBox>{error}</ErrorBox>}
            <div className="modal-footer">
              <button
                className="button"
                disabled={!!busy}
                onClick={() => setReplayOpen(false)}
              >
                Cancel
              </button>
              <button
                className="button primary"
                disabled={!!busy || !reviewer.trim()}
                onClick={() => void replay()}
              >
                {busy === "replay" ? (
                  <Spinner label="Reusing…" />
                ) : (
                  <>
                    <Check size={16} />
                    Reuse matching decisions
                  </>
                )}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
function RinkLogo() {
  return (
    <svg
      className="rink-logo"
      viewBox="0 0 40 40"
      fill="none"
      aria-hidden="true"
    >
      <rect x="1" y="1" width="38" height="38" rx="11" fill="currentColor" />
      <rect
        x="8"
        y="10"
        width="24"
        height="20"
        rx="8"
        stroke="var(--rink-ink, #183d33)"
        strokeWidth="1.8"
      />
      <path d="M20 10V30" stroke="var(--rink-ink, #183d33)" strokeWidth="1.8" />
      <circle
        cx="20"
        cy="20"
        r="4"
        stroke="var(--rink-ink, #183d33)"
        strokeWidth="1.8"
      />
    </svg>
  );
}
function ArrowUpRightIcon() {
  return <ExternalLink size={13} />;
}
