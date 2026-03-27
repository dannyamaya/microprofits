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
              <th>SL</th>
              <th>TP</th>
              <th>P&L</th>
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
                <td className="mono">{p.profit_level?.toFixed(2) ?? "—"}</td>
                <td className={`mono ${p.upl >= 0 ? "positive" : "negative"}`}>
                  ${p.upl?.toFixed(2)}
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
