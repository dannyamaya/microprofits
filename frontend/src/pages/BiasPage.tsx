import { useEffect, useState } from "react";
import {
  getBiasConfig,
  updateBiasConfig,
  getBiasInstruments,
  updateBiasInstrument,
  getBiasPositions,
  getBiasTrades,
  getBiasStats,
  getBiasAudit,
  getBiasAccount,
  getBiasSignals,
  getHeadlines,
} from "../lib/api.ts";
import "../components/styles.css";

export default function BiasPage() {
  const [config, setConfig] = useState<any>(null);
  const [instruments, setInstruments] = useState<any[]>([]);
  const [positions, setPositions] = useState<any[]>([]);
  const [trades, setTrades] = useState<any[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [audit, setAudit] = useState<any[]>([]);
  const [account, setAccount] = useState<any>(null);
  const [signals, setSignals] = useState<any>({});
  const [headlines, setHeadlines] = useState<any[]>([]);

  const refresh = async () => {
    try {
      const [cfg, inst, pos, tr, st, aud, acc, sig, hl] = await Promise.all([
        getBiasConfig(),
        getBiasInstruments(),
        getBiasPositions(),
        getBiasTrades(),
        getBiasStats(),
        getBiasAudit(),
        getBiasAccount(),
        getBiasSignals(),
        getHeadlines(),
      ]);
      setConfig(cfg);
      setInstruments(inst);
      setPositions(pos);
      setTrades(tr);
      setStats(st);
      setAudit(aud);
      setAccount(acc);
      setSignals(sig);
      setHeadlines(hl);
    } catch {
      // retry next poll
    }
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  if (!config) {
    return <div className="loading">Connecting to Bias Strategy...</div>;
  }

  const toggleEnabled = async () => {
    await updateBiasConfig({ enabled: !config.enabled });
    refresh();
  };

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className="header-left">
          <h1 className="bias-title">Bias Strategy</h1>
          <nav className="header-nav">
            <a href="#/" className="nav-link">Scalper</a>
            <a href="#/bias" className="nav-link nav-active">Bias</a>
          </nav>
          <span className={`status-badge ${config.enabled ? "on" : "off"}`}>
            {config.enabled ? "ENABLED" : "DISABLED"}
          </span>
        </div>
        <div className="header-center">
          <div className="stat">
            <span className="label">Balance</span>
            <span className="value">${account?.balance?.toFixed(2) ?? "—"}</span>
          </div>
          <div className="stat">
            <span className="label">Available</span>
            <span className="value">${account?.available?.toFixed(2) ?? "—"}</span>
          </div>
          <div className="stat">
            <span className="label">Positions</span>
            <span className="value">{positions.length}</span>
          </div>
          <div className="stat">
            <span className="label">P&L</span>
            <span className={`value ${(account?.profit_loss ?? 0) >= 0 ? "positive" : "negative"}`}>
              ${account?.profit_loss?.toFixed(2) ?? "0.00"}
            </span>
          </div>
        </div>
        <div className="header-right">
          <button
            className={`btn ${config.enabled ? "btn-stop" : "btn-start"}`}
            onClick={toggleEnabled}
          >
            {config.enabled ? "Disable" : "Enable"}
          </button>
        </div>
      </header>

      {/* Signals + Config Grid */}
      <div className="grid">
        {/* Current Signals */}
        <div className="panel">
          <h2>Current Bias Signals</h2>
          {instruments.length === 0 ? (
            <p className="empty">No instruments configured</p>
          ) : (
            <div className="bias-signals">
              {instruments.map((inst) => {
                const sig = findSignalForEpic(signals, inst.epic);
                return (
                  <BiasSignalCard
                    key={inst.epic}
                    instrument={inst}
                    signal={sig}
                  />
                );
              })}
            </div>
          )}
        </div>

        {/* Config + Stats */}
        <div className="panel">
          <h2>Configuration</h2>
          <BiasConfigPanel
            config={config}
            instruments={instruments}
            stats={stats}
            onRefresh={refresh}
          />
        </div>
      </div>

      {/* Open Positions */}
      <div className="panel">
        <h2>Open Positions</h2>
        {positions.length === 0 ? (
          <p className="empty">No open bias positions</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Epic</th>
                <th>Direction</th>
                <th>Size</th>
                <th>Entry</th>
                <th>SL</th>
                <th>TP</th>
                <th>UPL</th>
                <th>Signal</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.deal_id}>
                  <td className="mono">{p.epic}</td>
                  <td>
                    <span className={p.direction === "BUY" ? "positive" : "negative"}>
                      {p.direction}
                    </span>
                  </td>
                  <td className="mono">{p.size}</td>
                  <td className="mono">{p.entry_price?.toFixed(2)}</td>
                  <td className="mono">{p.stop_level?.toFixed(2) ?? "—"}</td>
                  <td className="mono">{p.profit_level?.toFixed(2) ?? "trail"}</td>
                  <td className={`mono ${(p.upl ?? 0) >= 0 ? "positive" : "negative"}`}>
                    ${p.upl?.toFixed(2) ?? "—"}
                  </td>
                  <td>
                    {p.signal_expired ? (
                      <span className="badge badge-yellow">EXPIRED</span>
                    ) : (
                      <span className="badge badge-green">ACTIVE</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Headlines */}
      <div className="panel">
        <h2>Headlines Feed</h2>
        {headlines.length === 0 ? (
          <p className="empty">No headlines yet</p>
        ) : (
          <div className="headlines-list">
            {headlines.slice(0, 20).map((h, i) => (
              <div key={h.id || i} className="headline-item">
                <span className="headline-time">{formatTime(h.created_at)}</span>
                <span className="headline-text">{h.text}</span>
                {h.likes > 0 && <span className="headline-engagement">{h.likes}L</span>}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Trade History */}
      <div className="panel">
        <h2>Trade History</h2>
        {trades.length === 0 ? (
          <p className="empty">No closed bias trades yet</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Epic</th>
                <th>Direction</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>P&L</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id}>
                  <td className="mono">{formatTime(t.closed_at)}</td>
                  <td className="mono">{t.epic}</td>
                  <td>
                    <span className={t.direction === "BUY" ? "positive" : "negative"}>
                      {t.direction}
                    </span>
                  </td>
                  <td className="mono">{t.entry_price?.toFixed(2)}</td>
                  <td className="mono">{t.exit_price?.toFixed(2) ?? "—"}</td>
                  <td className={`mono ${(t.pnl ?? 0) >= 0 ? "positive" : "negative"}`}>
                    ${t.pnl?.toFixed(2) ?? "—"}
                  </td>
                  <td>
                    <span className={`badge ${t.exit_reason?.includes("TP") ? "badge-green" : "badge-red"}`}>
                      {t.exit_reason}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Audit Log */}
      <div className="panel">
        <h2>Audit Log</h2>
        {audit.length === 0 ? (
          <p className="empty">No bias events yet</p>
        ) : (
          <div className="audit-list">
            {audit.slice(0, 50).map((a, i) => (
              <div key={a.id || i} className="audit-item">
                <span className="audit-time">{formatTime(a.ts)}</span>
                <span className={`audit-event ${auditColor(a.event)}`}>{a.event}</span>
                <span className="audit-epic">{a.epic}</span>
                <span className="audit-detail">
                  {typeof a.detail === "object" ? summarizeDetail(a.detail) : ""}
                </span>
                {a.pnl != null && (
                  <span className={`mono ${a.pnl >= 0 ? "positive" : "negative"}`}>
                    ${a.pnl?.toFixed(2)}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// -- Sub-components --

function BiasSignalCard({ instrument, signal }: { instrument: any; signal: any }) {
  const hasSignal = signal !== null;
  const direction = hasSignal ? signal.bias : "—";
  const confidence = hasSignal ? signal.confidence : 0;
  const inverted = instrument.inverted;

  // What direction would we actually trade?
  let tradeDir = "—";
  if (hasSignal && direction !== "NEUTRAL") {
    if (direction === "BULLISH") tradeDir = inverted ? "SELL" : "BUY";
    if (direction === "BEARISH") tradeDir = inverted ? "BUY" : "SELL";
  }

  return (
    <div className={`bias-signal-card ${hasSignal ? "" : "bias-signal-empty"}`}>
      <div className="bias-signal-header">
        <span className="bias-signal-epic">{instrument.epic}</span>
        {inverted && <span className="badge badge-yellow">INVERTED</span>}
        {!instrument.enabled && <span className="badge badge-gray">OFF</span>}
      </div>

      {hasSignal ? (
        <>
          <div className="bias-signal-direction">
            <span className={`bias-arrow ${direction === "BULLISH" ? "positive" : direction === "BEARISH" ? "negative" : ""}`}>
              {direction === "BULLISH" ? "BULLISH" : direction === "BEARISH" ? "BEARISH" : "NEUTRAL"}
            </span>
            <span className="bias-confidence">
              {"*".repeat(confidence)}{"·".repeat(5 - confidence)}
            </span>
          </div>

          <div className="bias-signal-trade">
            Trade: <span className={tradeDir === "BUY" ? "positive" : tradeDir === "SELL" ? "negative" : ""}>{tradeDir}</span>
            {inverted && <span className="bias-inverted-note">(inverted)</span>}
          </div>

          <div className="bias-signal-levels">
            {signal.price_target && <div>TP: <span className="mono">{signal.price_target}</span></div>}
            {signal.stop_loss_price && <div>SL: <span className="mono">{signal.stop_loss_price}</span></div>}
            {signal.key_support && <div>Support: <span className="mono">{signal.key_support}</span></div>}
            {signal.key_resistance && <div>Resistance: <span className="mono">{signal.key_resistance}</span></div>}
          </div>

          {signal.catalyst && (
            <div className="bias-signal-catalyst">{signal.catalyst}</div>
          )}

          <div className="bias-signal-time">
            {signal.created_at && <span>Received: {formatTime(signal.created_at)}</span>}
            {signal.expires_at && <span> | Expires: {formatTime(signal.expires_at)}</span>}
          </div>
        </>
      ) : (
        <p className="empty">No active signal</p>
      )}
    </div>
  );
}

function BiasConfigPanel({
  config,
  instruments,
  stats,
  onRefresh,
}: {
  config: any;
  instruments: any[];
  stats: any;
  onRefresh: () => void;
}) {
  const [threshold, setThreshold] = useState(config.confidence_threshold);
  const [trailPct, setTrailPct] = useState(config.trail_pct);
  useEffect(() => {
    setThreshold(config.confidence_threshold);
    setTrailPct(config.trail_pct);
  }, [config]);

  const saveConfig = async () => {
    await updateBiasConfig({ confidence_threshold: threshold, trail_pct: trailPct });
    onRefresh();
  };

  const toggleInstrument = async (epic: string, field: string, value: any) => {
    await updateBiasInstrument(epic, { [field]: value });
    onRefresh();
  };

  return (
    <>
      <div className="config-grid">
        <label>
          Confidence Threshold
          <input
            type="number"
            min={1}
            max={5}
            value={threshold}
            onChange={(e) => setThreshold(parseInt(e.target.value))}
            onBlur={saveConfig}
          />
        </label>
        <label>
          Trail % (fallback)
          <input
            type="number"
            min={10}
            max={95}
            step={5}
            value={trailPct}
            onChange={(e) => setTrailPct(parseFloat(e.target.value))}
            onBlur={saveConfig}
          />
        </label>
      </div>

      <h3>Notifications</h3>
      <p className="config-hint">
        Discord: {config.discord_active ? "Active" : "Not configured (set DISCORD_WEBHOOK_URL in .env)"}
        {" | "} X Headlines: {config.x_bearer_token !== undefined ? "Set via .env" : "Set X_BEARER_TOKEN in .env"}
      </p>

      <h3>Instruments</h3>
      <div className="symbol-list">
        {instruments.map((inst) => (
          <div key={inst.epic} className="symbol-row">
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className="symbol-epic">{inst.epic}</span>
              {inst.inverted && <span className="badge badge-yellow">INV</span>}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <label className="trail-pct-label">
                Qty
                <input
                  className="trail-pct-input"
                  type="number"
                  step={0.1}
                  min={0.1}
                  value={inst.num_contracts}
                  onChange={(e) =>
                    toggleInstrument(inst.epic, "num_contracts", parseFloat(e.target.value))
                  }
                />
              </label>
              <label className="trail-pct-label">
                Trail%
                <input
                  className="trail-pct-input"
                  type="number"
                  step={5}
                  min={10}
                  max={95}
                  value={inst.trail_pct ?? config.trail_pct}
                  onChange={(e) =>
                    toggleInstrument(inst.epic, "trail_pct", parseFloat(e.target.value))
                  }
                />
              </label>
              <button
                className={`btn btn-sm ${inst.inverted ? "btn-on" : "btn-off"}`}
                onClick={() => toggleInstrument(inst.epic, "inverted", !inst.inverted)}
              >
                {inst.inverted ? "INV" : "STD"}
              </button>
              <button
                className={`btn btn-sm ${inst.enabled ? "btn-on" : "btn-off"}`}
                onClick={() => toggleInstrument(inst.epic, "enabled", !inst.enabled)}
              >
                {inst.enabled ? "ON" : "OFF"}
              </button>
            </div>
          </div>
        ))}
      </div>

      {stats && (
        <>
          <h3>Performance</h3>
          <div className="stats-grid">
            <div className="stat-card">
              <span className="stat-label">Total PnL</span>
              <span className={`stat-value ${stats.total_pnl >= 0 ? "positive" : "negative"}`}>
                ${stats.total_pnl?.toFixed(2)}
              </span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Win Rate</span>
              <span className="stat-value">{stats.win_rate?.toFixed(1)}%</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Trades</span>
              <span className="stat-value">{stats.total}</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Avg Win / Loss</span>
              <span className="stat-value">
                <span className="positive">${stats.avg_win?.toFixed(2)}</span>
                {" / "}
                <span className="negative">${stats.avg_loss?.toFixed(2)}</span>
              </span>
            </div>
          </div>
        </>
      )}
    </>
  );
}

// -- Helpers --

// Map epic → possible instrument names in the bias API response
const EPIC_TO_INSTRUMENTS: Record<string, string[]> = {
  US100: ["NQ", "US100"],
  OIL_CRUDE: ["OIL", "CL", "OIL_CRUDE"],
};

function findSignalForEpic(signals: any, epic: string): any {
  if (!signals || typeof signals !== "object") return null;
  const names = EPIC_TO_INSTRUMENTS[epic] || [epic];
  for (const name of names) {
    if (signals[name]) return signals[name];
  }
  return null;
}

function formatTime(ts: string | null): string {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    const now = new Date();
    const diff = (now.getTime() - d.getTime()) / 1000;

    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;

    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return ts;
  }
}

function auditColor(event: string): string {
  if (event.includes("ENTRY")) return "positive";
  if (event.includes("BLOCKED") || event.includes("MARGIN")) return "negative";
  if (event.includes("EXPIRED") || event.includes("CONTRA")) return "audit-warn";
  if (event.includes("TP_HIT")) return "positive";
  if (event.includes("SL_HIT")) return "negative";
  return "";
}

function summarizeDetail(detail: any): string {
  if (!detail || typeof detail !== "object") return "";
  const parts: string[] = [];
  if (detail.direction) parts.push(detail.direction);
  if (detail.bias) parts.push(`bias=${detail.bias}`);
  if (detail.confidence) parts.push(`conf=${detail.confidence}`);
  if (detail.reason) parts.push(detail.reason);
  if (detail.pnl != null) parts.push(`$${detail.pnl}`);
  if (detail.catalyst) parts.push(detail.catalyst.slice(0, 60));
  return parts.join(" | ");
}
