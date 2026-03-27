interface Props {
  positions: any[];
}

export default function PositionTable({ positions }: Props) {
  return (
    <div className="panel">
      <h2>Open Positions ({positions.length})</h2>
      {positions.length === 0 ? (
        <p className="empty">No open positions</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Epic</th>
              <th>Size</th>
              <th>Entry</th>
              <th>Current SL</th>
              <th>P&L</th>
              <th>Locked</th>
              <th>Trail</th>
              <th>Opened</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.deal_id}>
                <td className="mono">{p.epic}</td>
                <td>{p.size}</td>
                <td className="mono">{p.entry_price?.toFixed(2)}</td>
                <td className="mono">{p.stop_level?.toFixed(2)}</td>
                <td className={`mono ${p.upl >= 0 ? "positive" : "negative"}`}>
                  ${p.upl?.toFixed(2)}
                </td>
                <td className={`mono ${p.locked_profit > 0 ? "positive" : ""}`}>
                  {p.locked_profit > 0 ? `$${p.locked_profit.toFixed(2)}` : "—"}
                </td>
                <td>
                  {p.trail_locks > 0 ? (
                    <span className="badge badge-green">{p.trail_locks}x</span>
                  ) : p.breakeven_hit ? (
                    <span className="badge badge-blue">BE</span>
                  ) : (
                    <span className="badge badge-gray">—</span>
                  )}
                </td>
                <td>{new Date(p.opened_at).toLocaleTimeString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
