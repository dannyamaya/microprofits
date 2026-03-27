interface Props {
  stats: any;
}

export default function PnlSummary({ stats }: Props) {
  if (!stats) return null;

  const pnlClass = stats.total_pnl >= 0 ? "positive" : "negative";

  return (
    <div className="panel pnl-panel">
      <h2>Performance</h2>
      <div className="stats-grid">
        <div className="stat-card">
          <span className="stat-label">Total P&L</span>
          <span className={`stat-value ${pnlClass}`}>
            ${stats.total_pnl?.toFixed(2)}
          </span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Win Rate</span>
          <span className="stat-value">{stats.win_rate?.toFixed(1)}%</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Total Trades</span>
          <span className="stat-value">{stats.total}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Wins / Losses</span>
          <span className="stat-value">
            {stats.wins} / {stats.losses}
          </span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Avg Win</span>
          <span className="stat-value positive">
            ${stats.avg_win?.toFixed(2)}
          </span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Avg Loss</span>
          <span className="stat-value negative">
            ${stats.avg_loss?.toFixed(2)}
          </span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Best Trade</span>
          <span className="stat-value positive">
            ${stats.best_trade?.toFixed(2)}
          </span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Worst Trade</span>
          <span className="stat-value negative">
            ${stats.worst_trade?.toFixed(2)}
          </span>
        </div>
      </div>
    </div>
  );
}
