import { useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  FileUp,
  FlaskConical,
  RefreshCw,
  Upload,
} from "lucide-react";
import { api } from "./api";
import type { RunDetail } from "./types";
import { ErrorBox, Modal, Spinner } from "./ui";

export default function ImportDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (run: RunDetail) => void;
}) {
  const [mode, setMode] = useState<"files" | "demo">("files");
  const [name, setName] = useState("");
  const [scorer, setScorer] = useState<File | null>(null);
  const [league, setLeague] = useState<File | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const choose = (
    file: File | undefined,
    setter: (value: File | null) => void,
  ) => {
    setError("");
    if (!file) {
      setter(null);
      return;
    }
    if (!file.name.toLowerCase().endsWith(".csv")) {
      setError("Choose a .csv file for each source.");
      setter(null);
      return;
    }
    if (file.size > 1024 * 1024) {
      setError("Each source file must be 1 MiB or smaller.");
      setter(null);
      return;
    }
    setter(file);
  };
  const create = async (variant?: "opening" | "followup" | "clean") => {
    setBusy(variant || "files");
    setError("");
    try {
      let next: RunDetail;
      if (variant) next = await api.demo(variant);
      else {
        if (!scorer || !league || !name.trim())
          throw new Error("Provide a name and both CSV files.");
        const decode = async (file: File) =>
          new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(
            await file.arrayBuffer(),
          );
        let texts: string[];
        try {
          texts = await Promise.all([decode(scorer), decode(league)]);
        } catch {
          throw new Error("Both source files must contain valid UTF-8 text.");
        }
        next = await api.create(name.trim(), texts[0], texts[1]);
      }
      onCreated(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create this run.");
    } finally {
      setBusy("");
    }
  };
  return (
    <Modal
      title="Start a reconciliation"
      subtitle="New run"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <div className="modal-body">
        <p className="muted">
          Compare two exports. Preserve the evidence. Review what does not
          agree.
        </p>
        <div className="segmented" aria-label="Source type">
          <button
            className={mode === "files" ? "active" : ""}
            onClick={() => setMode("files")}
          >
            Upload CSV files
          </button>
          <button
            className={mode === "demo" ? "active" : ""}
            onClick={() => setMode("demo")}
          >
            <FlaskConical size={15} />
            Try a demo
          </button>
        </div>
        {error && <ErrorBox>{error}</ErrorBox>}
        {mode === "files" ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void create();
            }}
          >
            <label className="field-label" htmlFor="run-name">
              Run name
            </label>
            <input
              id="run-name"
              maxLength={120}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Weekend results · September 20"
              required
            />
            <div className="upload-grid">
              {[
                {
                  id: "scorer",
                  label: "Scorer export",
                  file: scorer,
                  setter: setScorer,
                },
                {
                  id: "league",
                  label: "League export",
                  file: league,
                  setter: setLeague,
                },
              ].map((x) => (
                <label
                  key={x.id}
                  className={`file-upload ${x.file ? "has-file" : ""}`}
                >
                  <FileUp size={26} />
                  <strong>{x.label}</strong>
                  <span>{x.file ? x.file.name : "Choose a CSV file"}</span>
                  <small>
                    {x.file
                      ? `${(x.file.size / 1024).toFixed(1)} KiB`
                      : "Up to 1 MiB · 5,000 rows"}
                  </small>
                  <input
                    aria-label={x.label}
                    type="file"
                    accept=".csv,text/csv"
                    onChange={(e) => choose(e.target.files?.[0], x.setter)}
                  />
                </label>
              ))}
            </div>
            <div className="info-callout">
              <ArrowDownToLine size={18} />
              <span>
                Use the same <code>season</code> and <code>game_id</code> in
                both feeds.{" "}
                <a href="/api/template" download>
                  Download CSV template
                </a>
              </span>
            </div>
            <p className="small muted">
              Malformed files are rejected. Invalid records remain visible for
              review. Upload only data you are authorized to use.
            </p>
            <div className="modal-footer">
              <button
                type="button"
                className="button"
                onClick={onClose}
                disabled={!!busy}
              >
                Cancel
              </button>
              <button
                className="button primary"
                disabled={!!busy || !scorer || !league || !name.trim()}
              >
                {busy ? (
                  <Spinner label="Reconciling…" />
                ) : (
                  <>
                    <Upload size={16} />
                    Reconcile files
                  </>
                )}
              </button>
            </div>
          </form>
        ) : (
          <div className="demo-list">
            <p className="small muted">
              Fictional teams and deliberately inconsistent records. No league
              affiliation or live sports data.
            </p>
            {(
              [
                {
                  id: "opening",
                  title: "Opening release",
                  description:
                    "Start with score conflicts, a missing record, duplicate entries, and invalid values.",
                },
                {
                  id: "followup",
                  title: "Follow-up release",
                  description:
                    "Reuse reviewed decisions for unchanged evidence. Changed evidence stays open.",
                },
                {
                  id: "clean",
                  title: "Clean release",
                  description:
                    "See fully reconciled results and download a traceable export.",
                },
              ] as const
            ).map((x) => (
              <div className="demo-choice" key={x.id}>
                <div className="demo-choice-icon">
                  {x.id === "followup" ? (
                    <RefreshCw size={20} />
                  ) : (
                    <FlaskConical size={20} />
                  )}
                </div>
                <div>
                  <h3>{x.title}</h3>
                  <p>{x.description}</p>
                  <div className="sample-links">
                    <a href={`/api/demo-files/${x.id}/scorer`} download>
                      Scorer CSV
                    </a>
                    <span>·</span>
                    <a href={`/api/demo-files/${x.id}/league`} download>
                      League CSV
                    </a>
                  </div>
                </div>
                <button
                  className="icon-button outlined"
                  aria-label={`Load ${x.title.toLowerCase()}`}
                  disabled={!!busy}
                  onClick={() => void create(x.id)}
                >
                  {busy === x.id ? (
                    <Spinner label="" />
                  ) : (
                    <ArrowRight size={18} />
                  )}
                </button>
              </div>
            ))}
            <p className="small muted">
              Loading the same pair returns its existing run, including saved
              decisions.
            </p>
          </div>
        )}
      </div>
    </Modal>
  );
}
