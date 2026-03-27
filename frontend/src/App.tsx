import { useEffect, useState } from "react";
import { getStatus, getConfig, getPositions, getStats, getTrades, getHeatmap } from "./lib/api.ts";
import Header from "./components/Header.tsx";
import ConfigPanel from "./components/ConfigPanel.tsx";
import PositionTable from "./components/PositionTable.tsx";
import PnlSummary from "./components/PnlSummary.tsx";
import TradeHistory from "./components/TradeHistory.tsx";
import Heatmap from "./components/Heatmap.tsx";
import "./components/styles.css";

export default function App() {
  const [status, setStatus] = useState<any>(null);
  const [config, setConfig] = useState<any>(null);
  const [positions, setPositions] = useState<any[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [trades, setTrades] = useState<any[]>([]);
  const [heatmap, setHeatmap] = useState<any[]>([]);

  const refresh = async () => {
    try {
      const [s, c, p, st, t, h] = await Promise.all([
        getStatus(),
        getConfig(),
        getPositions(),
        getStats(),
        getTrades(),
        getHeatmap(),
      ]);
      setStatus(s);
      setConfig(c);
      setPositions(p);
      setStats(st);
      setTrades(t);
      setHeatmap(h);
    } catch {
      // will retry next poll
    }
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  if (!status || !config) {
    return <div className="loading">Connecting to Microprofits...</div>;
  }

  return (
    <div className="app">
      <Header status={status} onRefresh={refresh} />
      <div className="grid">
        <ConfigPanel config={config} onRefresh={refresh} />
        <PnlSummary stats={stats} />
      </div>
      <PositionTable positions={positions} />
      <Heatmap data={heatmap} />
      <TradeHistory trades={trades} />
    </div>
  );
}
