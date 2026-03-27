interface Props {
  data: any[];
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

export default function Heatmap({ data }: Props) {
  if (!data.length) {
    return (
      <div className="panel">
        <h2>Hourly Profitability</h2>
        <p className="empty">No data yet — trades will populate this heatmap</p>
      </div>
    );
  }

  const lookup = new Map<string, { pnl: number; count: number }>();
  let maxAbs = 1;
  for (const d of data) {
    const key = `${d.day_of_week}-${d.hour}`;
    lookup.set(key, { pnl: d.pnl, count: d.trade_count });
    if (Math.abs(d.pnl) > maxAbs) maxAbs = Math.abs(d.pnl);
  }

  const cellColor = (pnl: number): string => {
    if (pnl === 0) return "var(--bg-secondary)";
    const intensity = Math.min(Math.abs(pnl) / maxAbs, 1);
    const alpha = 0.2 + intensity * 0.6;
    return pnl > 0
      ? `rgba(34, 197, 94, ${alpha})`
      : `rgba(239, 68, 68, ${alpha})`;
  };

  return (
    <div className="panel">
      <h2>Hourly Profitability</h2>
      <div className="heatmap-container">
        <div className="heatmap-grid">
          <div className="heatmap-corner" />
          {HOURS.map((h) => (
            <div key={h} className="heatmap-hour">
              {h.toString().padStart(2, "0")}
            </div>
          ))}
          {DAYS.map((day, di) => (
            <>
              <div key={`day-${di}`} className="heatmap-day">
                {day}
              </div>
              {HOURS.map((h) => {
                const cell = lookup.get(`${di}-${h}`);
                return (
                  <div
                    key={`${di}-${h}`}
                    className="heatmap-cell"
                    style={{ backgroundColor: cellColor(cell?.pnl ?? 0) }}
                    title={
                      cell
                        ? `${day} ${h}:00 — $${cell.pnl.toFixed(2)} (${cell.count} trades)`
                        : `${day} ${h}:00 — no trades`
                    }
                  />
                );
              })}
            </>
          ))}
        </div>
      </div>
    </div>
  );
}
