import { useState, useEffect } from "react";
import { updateConfig, getSymbols, updateSymbol } from "../lib/api.ts";

interface Props {
  config: any;
  onRefresh: () => void;
}

export default function ConfigPanel({ config, onRefresh }: Props) {
  const [profitTarget, setProfitTarget] = useState(config.profit_target);
  const [stopLoss, setStopLoss] = useState(config.stop_loss);
  const [maxPositions, setMaxPositions] = useState(config.max_positions);
  const [numContracts, setNumContracts] = useState(config.num_contracts);
  const [emaOn, setEmaOn] = useState(config.ema_filter_on);
  const [emaPeriod, setEmaPeriod] = useState(config.ema_period);
  const [cooldown, setCooldown] = useState(config.entry_cooldown);
  const [symbols, setSymbols] = useState<any[]>([]);

  useEffect(() => {
    setProfitTarget(config.profit_target);
    setStopLoss(config.stop_loss);
    setMaxPositions(config.max_positions);
    setNumContracts(config.num_contracts);
    setEmaOn(config.ema_filter_on);
    setEmaPeriod(config.ema_period);
    setCooldown(config.entry_cooldown);
  }, [config]);

  useEffect(() => {
    getSymbols().then(setSymbols);
  }, []);

  const save = async () => {
    await updateConfig({
      profit_target: profitTarget,
      stop_loss: stopLoss,
      max_positions: maxPositions,
      num_contracts: numContracts,
      ema_filter_on: emaOn,
      ema_period: emaPeriod,
      entry_cooldown: cooldown,
    });
    onRefresh();
  };

  const toggleSymbol = async (epic: string, enabled: boolean) => {
    await updateSymbol(epic, { enabled });
    const updated = await getSymbols();
    setSymbols(updated);
  };

  const changeStrategy = async (epic: string, strategy: string) => {
    await updateSymbol(epic, { strategy });
    const updated = await getSymbols();
    setSymbols(updated);
  };

  const changeTrailPct = async (epic: string, trail_pct: number) => {
    await updateSymbol(epic, { trail_pct });
    const updated = await getSymbols();
    setSymbols(updated);
  };

  return (
    <div className="panel config-panel">
      <h2>Configuration</h2>
      <div className="config-grid">
        <label>
          Profit Target ($)
          <input
            type="number"
            step="0.5"
            value={profitTarget}
            onChange={(e) => setProfitTarget(Number(e.target.value))}
          />
        </label>
        <label>
          Stop Loss ($)
          <input
            type="number"
            step="0.5"
            value={stopLoss}
            onChange={(e) => setStopLoss(Number(e.target.value))}
          />
        </label>
        <label>
          Max Positions
          <input
            type="number"
            min="1"
            max="10"
            value={maxPositions}
            onChange={(e) => setMaxPositions(Number(e.target.value))}
          />
        </label>
        <label>
          Contracts
          <input
            type="number"
            step="0.5"
            min="0.5"
            value={numContracts}
            onChange={(e) => setNumContracts(Number(e.target.value))}
          />
        </label>
        <label>
          Entry Cooldown (s)
          <input
            type="number"
            min="5"
            max="300"
            value={cooldown}
            onChange={(e) => setCooldown(Number(e.target.value))}
          />
        </label>
        <label className="toggle-row">
          EMA Filter
          <input
            type="checkbox"
            checked={emaOn}
            onChange={(e) => setEmaOn(e.target.checked)}
          />
        </label>
        <label>
          EMA Period
          <input
            type="number"
            min="2"
            max="50"
            value={emaPeriod}
            onChange={(e) => setEmaPeriod(Number(e.target.value))}
          />
        </label>
      </div>

      <p className="config-hint">
        Trail mode: SL moves up by Profit Target each time UPL crosses a new increment
      </p>

      <button className="btn btn-primary" onClick={save}>
        Save Config
      </button>

      <h3>Symbols</h3>
      <div className="symbol-list">
        {symbols.map((s) => (
          <div key={s.epic} className="symbol-row">
            <span className="symbol-epic">{s.epic}</span>
            <select
              className="strategy-select"
              value={s.strategy || "scalper"}
              onChange={(e) => changeStrategy(s.epic, e.target.value)}
            >
              <option value="scalper">Scalper</option>
              <option value="asian_range">Asian Range</option>
            </select>
            <label className="trail-pct-label">
              Trail %
              <input
                type="number"
                className="trail-pct-input"
                min="0"
                max="80"
                step="5"
                value={s.trail_pct || 0}
                onChange={(e) => changeTrailPct(s.epic, Number(e.target.value))}
              />
            </label>
            <button
              className={`btn btn-sm ${s.enabled ? "btn-on" : "btn-off"}`}
              onClick={() => toggleSymbol(s.epic, !s.enabled)}
            >
              {s.enabled ? "ON" : "OFF"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
