import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getJSON } from "./api.js";
import { dayLabel, gwh, mw, pct } from "./format.js";

function Chart({ title, note, children }) {
  return (
    <figure className="figure">
      <figcaption>
        <strong>{title}</strong>
        {note && <span className="muted small"> {note}</span>}
      </figcaption>
      <div className="chart">{children}</div>
    </figure>
  );
}

const axis = { fontSize: 12 };

export default function AccuracyView({ cutoff }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setError(null);
    getJSON("/accuracy", { cutoff })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [cutoff]);

  if (error) return <p className="notice bad">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;

  const o = data.overall;
  const daily = data.daily.map((d) => ({ ...d, day: d.delivery_date.slice(8) }));

  return (
    <section>
      <h2>How wrong was the forecast we had at {data.cutoff}?</h2>
      <p className="lede">
        Across {o.days} January delivery days, the forecast available at the cut-off was off by {mw(o.mae)} on average
        ({pct(o.wmape)} of output). Nine hours in ten were within {mw(o.p90_abs)}, and the worst miss was {mw(o.max_abs)}.
        On average it ran {o.bias >= 0 ? "high" : "low"} by {mw(Math.abs(o.bias))}.
      </p>

      {Math.abs(o.bias) > 0.7 * o.mae && (
        <p className="notice">
          Most of the error is one-directional: the forecast has consistently run {o.bias >= 0 ? "high" : "low"} (bias {mw(o.bias)}).
          That is a systematic gap rather than random noise. Its cause has not been confirmed in this app; the README reports a comparison
          with a second Elexon series (AGWS) which points to FUELHH measuring less wind than WINDFOR forecasts.
          In this sample a buffer sized from the forecast alone would have been too {o.bias >= 0 ? "small" : "large"}.
        </p>
      )}

      <dl className="facts">
        <div><dt>Mean absolute error</dt><dd>{mw(o.mae)}</dd></div>
        <div><dt>Root mean square error</dt><dd>{mw(o.rmse)}</dd><dd className="sub">penalises big misses</dd></div>
        <div><dt>Bias</dt><dd>{mw(o.bias)}</dd><dd className="sub">forecast minus actual</dd></div>
        <div><dt>Error vs output</dt><dd>{pct(o.wmape)}</dd><dd className="sub">weighted MAPE</dd></div>
        <div><dt>90th percentile miss</dt><dd>{mw(o.p90_abs)}</dd><dd className="sub">95th: {mw(o.p95_abs)}</dd></div>
      </dl>

      <p className="muted small">
        Sample: {data.samples.headline.hours} hours across {data.samples.headline.days} delivery days ({dayLabel(data.samples.headline.first_delivery)} to{" "}
        {dayLabel(data.samples.headline.last_delivery)}), each hour scored against the single forecast available at the {data.cutoff} cut-off.
        The daily, hourly and spread charts use this sample. The lead-time chart is different: it uses all {data.samples.horizon.forecasts.toLocaleString("en-GB")}{" "}
        published forecasts (up to {data.samples.horizon.max_lead_h} h ahead) for the same hours, so its values do not match the headline.
      </p>

      <Chart title="Error by delivery day" note="Bars: typical miss (MAE). Line: bias; above zero means the forecast was too high.">
        <ResponsiveContainer>
          <ComposedChart data={daily} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
            <CartesianGrid stroke="var(--rule)" vertical={false} />
            <XAxis dataKey="day" tick={axis} />
            <YAxis tick={axis} width={56} tickFormatter={(v) => `${(v / 1000).toFixed(1)}k`} />
            <Tooltip formatter={(v) => mw(v)} labelFormatter={(l) => `${l} Jan/Feb`} />
            <Legend />
            <Bar dataKey="mae" name="Typical miss (MAE)" fill="var(--forecast)" isAnimationActive={false} />
            <Line dataKey="bias" name="Bias" stroke="var(--warn)" strokeWidth={2} dot={{ r: 2 }} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </Chart>

      <Chart title="Daily volume miss" note="Forecast total minus actual total for the delivery day. Negative means more wind arrived than expected.">
        <ResponsiveContainer>
          <BarChart data={daily} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
            <CartesianGrid stroke="var(--rule)" vertical={false} />
            <XAxis dataKey="day" tick={axis} />
            <YAxis tick={axis} width={56} tickFormatter={(v) => `${(v / 1000).toFixed(0)} GWh`} />
            <Tooltip formatter={(v) => gwh(v)} labelFormatter={(l) => `Delivery day ${l}`} />
            <Bar dataKey="volume_error_mwh" name="Volume miss" isAnimationActive={false}>
              {daily.map((d) => (
                <Cell key={d.day} fill={d.volume_error_mwh >= 0 ? "var(--warn)" : "var(--forecast)"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Chart>

      <Chart
        title="Error by how far ahead the forecast was issued"
        note={`Every published forecast, not only the cut-off one (hover for the count per bar). Darker bars hold the ${data.lead_time_h.min}-${data.lead_time_h.max} h lead time of the ${data.cutoff} decision; buckets are wide, so they cover more than that.`}
      >
        <ResponsiveContainer>
          <BarChart data={data.horizon} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
            <CartesianGrid stroke="var(--rule)" vertical={false} />
            <XAxis dataKey="label" tick={axis} />
            <YAxis tick={axis} width={56} tickFormatter={(v) => `${(v / 1000).toFixed(1)}k`} />
            <Tooltip formatter={(v, name, item) => [mw(v), `${name}, ${item.payload.n.toLocaleString("en-GB")} forecasts`]} />
            <Bar dataKey="mae" name="Typical miss (MAE)" isAnimationActive={false}>
              {data.horizon.map((h) => (
                <Cell key={h.label} fill={h.decision_relevant ? "var(--forecast)" : "var(--faint)"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Chart>

      <Chart title="Spread of hourly errors" note="Centre bars near zero mean unbiased; a long tail to the right is the risk of buying too little.">
        <ResponsiveContainer>
          <BarChart data={data.histogram} margin={{ top: 8, right: 12, bottom: 0, left: 4 }} barCategoryGap={1}>
            <CartesianGrid stroke="var(--rule)" vertical={false} />
            <XAxis dataKey="mid" tick={axis} tickFormatter={(v) => `${(v / 1000).toFixed(1)}k`} />
            <YAxis tick={axis} width={40} />
            <Tooltip formatter={(v) => `${v} hours`} labelFormatter={(l) => `error near ${mw(l)}`} />
            <Bar dataKey="count" name="Hours" fill="var(--forecast)" isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </Chart>

      <Chart title="Error by hour of the day" note="Typical miss for each delivery hour (UTC).">
        <ResponsiveContainer>
          <BarChart data={data.by_hour} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
            <CartesianGrid stroke="var(--rule)" vertical={false} />
            <XAxis dataKey="hour" tick={axis} />
            <YAxis tick={axis} width={56} tickFormatter={(v) => `${(v / 1000).toFixed(1)}k`} />
            <Tooltip formatter={(v) => mw(v)} labelFormatter={(l) => `${String(l).padStart(2, "0")}:00`} />
            <Bar dataKey="mae" name="Typical miss (MAE)" fill="var(--forecast)" isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </Chart>
    </section>
  );
}
