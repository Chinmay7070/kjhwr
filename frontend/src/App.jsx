import { useEffect, useState } from "react";
import { getJSON } from "./api.js";
import DecisionView from "./DecisionView.jsx";
import AccuracyView from "./AccuracyView.jsx";

export default function App() {
  const [meta, setMeta] = useState(null);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState("decision");
  const [cutoff, setCutoff] = useState("09:00");

  useEffect(() => {
    getJSON("/meta")
      .then((m) => {
        setMeta(m);
        setCutoff(m.default_cutoff);
      })
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <main className="page"><p className="notice bad">Could not load data: {error}</p></main>;
  if (!meta) return <main className="page"><p className="muted">Loading…</p></main>;

  return (
    <main className="page">
      <header className="top">
        <div>
          <h1>Wind forecast desk</h1>
          <p className="muted">
            GB day-ahead purchasing, January 2026. National wind from Elexon (FUELHH actuals, WINDFOR forecasts).
          </p>
        </div>
        <label className="cutoff">
          Decision cut-off (UTC)
          <input type="time" value={cutoff} onChange={(e) => e.target.value && setCutoff(e.target.value)} />
        </label>
      </header>

      {meta.source === "synthetic" && (
        <p className="notice">
          Demo data: these numbers are synthetic. Run <code>python -m scripts.fetch_data</code> in <code>backend</code> to load real Elexon data.
        </p>
      )}

      <nav className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "decision"} onClick={() => setTab("decision")}>
          Tomorrow's wind
        </button>
        <button role="tab" aria-selected={tab === "accuracy"} onClick={() => setTab("accuracy")}>
          Forecast accuracy
        </button>
      </nav>

      {tab === "decision" ? (
        <DecisionView meta={meta} cutoff={cutoff} />
      ) : (
        <AccuracyView cutoff={cutoff} />
      )}
    </main>
  );
}
