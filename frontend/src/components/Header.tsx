import { startBot, stopBot, flattenAll } from "../lib/api.ts";

interface Props {
  status: any;
  onRefresh: () => void;
}

export default function Header({ status, onRefresh }: Props) {
  const enabled = status.bot_enabled;

  const toggle = async () => {
    if (enabled) {
      await stopBot();
    } else {
      await startBot();
    }
    onRefresh();
  };

  const flatten = async () => {
    if (confirm("Close ALL positions and disable bot?")) {
      await flattenAll();
      onRefresh();
    }
  };

  const fmtUptime = (secs: number) => {
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    return `${h}h ${m}m`;
  };

  const currentPage = window.location.pathname === "/bias" ? "bias" : "scalper";

  const navigate = (e: React.MouseEvent, path: string) => {
    e.preventDefault();
    window.history.pushState({}, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  };

  return (
    <header className="header">
      <div className="header-left">
        <h1>Microprofits</h1>
        <nav className="header-nav">
          <a
            href="/"
            onClick={(e) => navigate(e, "/")}
            className={`nav-link ${currentPage === "scalper" ? "nav-active" : ""}`}
          >
            Scalper
          </a>
          <a
            href="/bias"
            onClick={(e) => navigate(e, "/bias")}
            className={`nav-link ${currentPage === "bias" ? "nav-active" : ""}`}
          >
            Bias
          </a>
        </nav>
        <span className={`status-badge ${enabled ? "on" : "off"}`}>
          {enabled ? "RUNNING" : "STOPPED"}
        </span>
      </div>
      <div className="header-center">
        <div className="stat">
          <span className="label">Positions</span>
          <span className="value">{status.open_positions}</span>
        </div>
        <div className="stat">
          <span className="label">Balance</span>
          <span className="value">
            ${status.account?.balance?.toFixed(2) ?? "—"}
          </span>
        </div>
        <div className="stat">
          <span className="label">Available</span>
          <span className="value">
            ${status.account?.available?.toFixed(2) ?? "—"}
          </span>
        </div>
        <div className="stat">
          <span className="label">Uptime</span>
          <span className="value">{fmtUptime(status.uptime_seconds)}</span>
        </div>
      </div>
      <div className="header-right">
        <button className={`btn ${enabled ? "btn-stop" : "btn-start"}`} onClick={toggle}>
          {enabled ? "Stop Bot" : "Start Bot"}
        </button>
        <button className="btn btn-danger" onClick={flatten}>
          Flatten All
        </button>
      </div>
    </header>
  );
}
