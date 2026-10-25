import { useEffect, useMemo, useState } from "react";
import { Area, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getJSON } from "./api.js";
import { dayLabel, gwh, hhmm, mw, pct, timeLabel } from "./format.js";

export default function DecisionView({ meta, cutoff }) {
  const [date, setDate] = useState(meta.decision_dates[13]);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [showActual, setShowActual] = useState(true);

  useEffect(() => {
    setError(null);
    getJSON("/decision", { decision_date: date, cutoff })
      .then(setData)
      .catch((e) => {
        setData(null);
        setError(e.message);
      });
  }, [date, cutoff]);

  const chart = useMemo(
    () =>
      (data?.rows ?? []).map((r) => ({
        label: hhmm(r.start),
        forecast: r.forecast,
        actual: r.actual,
        band: r.band_low != null ? [r.band_low, r.band_high] : null,
      })),
    [data]
  );

  const s = data?.summary;
  const h = data?.history;

  return (
    <section>
      <div className="controls">
        <label>
          Decision day
          <select value={date} onChange={(e) => setDate(e.target.value)}>
            {meta.decision_dates.map((d) => (
              <option key={d} value={d}>{dayLabel(d)}</option>
            ))}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={showActual} onChange={(e) => setShowActual(e.target.checked)} />
          Show what actually happened
        </label>
      </div>

      {error && <p className="notice bad">{error}</p>}

      {data && (
        <>
          <h2>
            Buying on {dayLabel(data.decision_date)} for delivery on {dayLabel(data.delivery_date)}
          </h2>
          <p className="lede">
            At the {cutoff} cut-off, the latest forecast was issued {timeLabel(s.last_publish)}
            {s.first_publish !== s.last_publish && ` (some slots use a forecast from ${timeLabel(s.first_publish)})`}. It looks
            {" "}{s.min_horizon_h.toFixed(0)} to {s.max_horizon_h.toFixed(0)} hours ahead.
            Forecasts published after the cut-off are ignored, because the desk could not have seen them.
          </p>

          <dl className="facts">
            <div>
              <dt>Expected wind</dt>
              <dd>{gwh(s.forecast_mwh)}</dd>
              <dd className="sub">average {mw(s.avg_forecast_mw)}</dd>
            </div>
            <div>
              <dt>Cautious case</dt>
              <dd>{s.cautious_mwh != null ? gwh(s.cautious_mwh) : "not enough history"}</dd>
              <dd className="sub">
                {s.cautious_mwh != null ? `${gwh(s.forecast_mwh - s.cautious_mwh)} less than expected` : "needs 3 days of past errors"}
              </dd>
            </div>
            <div>
              <dt>Recent forecast error</dt>
              <dd>{h.mae != null ? `${mw(h.mae)} typical` : "n/a"}</dd>
              <dd className="sub">
                {h.mae != null ? `${pct(h.wmape)} of output, last ${h.days_used} days` : ""}
              </dd>
            </div>
            {s.actual_complete && s.forecast_complete && showActual && (
              <div>
                <dt>What happened</dt>
                <dd>{gwh(s.actual_mwh)}</dd>
                <dd className="sub">
                  forecast was {gwh(Math.abs(s.forecast_mwh - s.actual_mwh))} {s.forecast_mwh > s.actual_mwh ? "too high" : "too low"}
                </dd>
              </div>
            )}
          </dl>

          <div className="chart tall">
            <ResponsiveContainer>
              <ComposedChart data={chart} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
                <CartesianGrid stroke="var(--rule)" vertical={false} />
                <XAxis dataKey="label" interval={2} tick={{ fontSize: 12 }} />
                <YAxis tickFormatter={(v) => `${(v / 1000).toFixed(0)} GW`} tick={{ fontSize: 12 }} width={56} />
                <Tooltip formatter={(v) => (Array.isArray(v) ? `${mw(v[0])} to ${mw(v[1])}` : mw(v))} />
                <Legend />
                <Area dataKey="band" name="Likely range (80%)" stroke="none" fill="var(--band)" isAnimationActive={false} />
                <Line dataKey="forecast" name="Forecast at cut-off" stroke="var(--forecast)" strokeWidth={2.5} dot={false} isAnimationActive={false} />
                {showActual && (
                  <Line dataKey="actual" name="Actual (known after delivery)" stroke="var(--ink)" strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          <p className="muted small">
            The range uses the last {h.window_days} days of forecast errors from this same cut-off: actual output was within it in about 8 of 10
            hours. Error is forecast minus actual, so a positive recent bias ({mw(h.bias)}) means the forecast ran high and the desk would have bought too little.
          </p>
        </>
      )}
    </section>
  );
}
