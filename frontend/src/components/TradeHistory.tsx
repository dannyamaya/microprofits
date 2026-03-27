interface Props {
  trades: any[];
}

export default function TradeHistory({ trades }: Props) {
  return (
    <div className="panel">
      <h2>Trade History</h2>
      {trades.length === 0 ? (
        <p className="empty">No closed trades yet</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Epic</th>
              <th>Dir</th>
              <th>Size</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>P&L</th>
              <th>Reason</th>
              <th>Closed</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.id}>
                <td className="mono">{t.epic}</td>
                <td>{t.direction}</td>
                <td>{t.size}</td>
                <td className="mono">{t.entry_price?.toFixed(2)}</td>
                <td className="mono">{t.exit_price?.toFixed(2) ?? "—"}</td>
                <td
                  className={`mono ${
                    (t.pnl ?? 0) >= 0 ? "positive" : "negative"
                  }`}
                >
                  ${t.pnl?.toFixed(2) ?? "—"}
                </td>
                <td>
                  <span className={`badge badge-${badgeType(t.exit_reason)}`}>
                    {t.exit_reason}
                  </span>
                </td>
                <td>
                  {t.closed_at
                    ? new Date(t.closed_at).toLocaleString()
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function badgeType(reason: string): string {
  if (reason === "TP_HIT" || reason === "SCALING") return "green";
  if (reason === "SL_HIT") return "red";
  return "gray";
}
