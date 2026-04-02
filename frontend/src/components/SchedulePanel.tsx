import { useState, useEffect } from "react";

interface StrategySchedule {
  name: string;
  epic: string;
  account: string;
  description: string;
  windows: { label: string; utcHours: number[] }[];
}

const STRATEGIES: StrategySchedule[] = [
  {
    name: "Scalper",
    epic: "US100",
    account: "microprofits",
    description: "Momentum scalping — safe hours only (post-loss 60s cooldown)",
    windows: [
      { label: "Evening momentum", utcHours: [0, 1] },
      { label: "Late night", utcHours: [6] },
      { label: "US pre-market + open", utcHours: [13, 14, 15, 16] },
      { label: "US afternoon", utcHours: [19] },
    ],
  },
  {
    name: "Asian Range",
    epic: "GOLD",
    account: "asian_range",
    description: "Asian session range breakout at London open",
    windows: [
      { label: "Building range", utcHours: [0, 1, 2, 3, 4, 5, 6] },
      { label: "Breakout window", utcHours: [8, 9, 10, 11] },
    ],
  },
];

const COL_OFFSET = -5;

function utcToColombia(hour: number): number {
  return ((hour + COL_OFFSET) % 24 + 24) % 24;
}

function fmtHour(h: number): string {
  const suffix = h >= 12 ? "PM" : "AM";
  const display = h === 0 ? 12 : h > 12 ? h - 12 : h;
  return `${display}${suffix}`;
}

function fmtRange(hours: number[], tz: "utc" | "col"): string {
  const converted = tz === "col" ? hours.map(utcToColombia) : hours;
  const start = converted[0];
  const end = (converted[converted.length - 1] + 1) % 24;
  return `${fmtHour(start)}-${fmtHour(end)}`;
}

function getAllSafeHours(strategy: StrategySchedule): Set<number> {
  const hours = new Set<number>();
  for (const w of strategy.windows) {
    for (const h of w.utcHours) hours.add(h);
  }
  return hours;
}

interface Props {
  status?: any;
}

export default function SchedulePanel({ status }: Props) {
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 10000);
    return () => clearInterval(id);
  }, []);

  const utcHour = now.getUTCHours();
  const colHour = utcToColombia(utcHour);

  return (
    <div className="panel schedule-panel">
      <h2>Strategy Schedule</h2>
      <p className="schedule-clock">
        <span className="clock-label">Now:</span>{" "}
        <span className="clock-time">{fmtHour(colHour)} COL</span>
        <span className="clock-sep">/</span>
        <span className="clock-time">{fmtHour(utcHour)} UTC</span>
      </p>

      <div className="schedule-list">
        {STRATEGIES.map((s) => {
          const safeHours = getAllSafeHours(s);
          const active = safeHours.has(utcHour);
          const currentWindow = s.windows.find((w) => w.utcHours.includes(utcHour));

          // Use backend status for scalper if available
          const isScalper = s.epic === "US100";
          const scalperActive = isScalper && status?.scalper_active !== undefined
            ? status.scalper_active
            : active;
          const isActive = isScalper ? scalperActive : active;

          return (
            <div key={s.epic} className={`schedule-card ${isActive ? "schedule-active" : "schedule-inactive"}`}>
              <div className="schedule-header">
                <div className="schedule-title">
                  <span className={`schedule-dot ${isActive ? "dot-on" : "dot-off"}`} />
                  <strong>{s.name}</strong>
                  <span className="schedule-epic">{s.epic}</span>
                  <span className={`schedule-status-badge ${isActive ? "badge-active" : "badge-inactive"}`}>
                    {isActive ? "ACTIVE" : "WAITING"}
                  </span>
                </div>
                <span className="schedule-account">{s.account}</span>
              </div>

              {currentWindow && isActive && (
                <div className="schedule-phase">
                  {currentWindow.label}
                </div>
              )}

              <div className="schedule-times">
                {s.windows.map((w) => {
                  const windowActive = w.utcHours.includes(utcHour);
                  return (
                    <div key={w.label} className={`schedule-time-row ${windowActive ? "time-row-active" : ""}`}>
                      <span className="schedule-phase-label">{w.label}</span>
                      <span className="schedule-hours">
                        {fmtRange(w.utcHours, "col")} COL
                      </span>
                      <span className="schedule-hours-utc">
                        {fmtRange(w.utcHours, "utc")} UTC
                      </span>
                    </div>
                  );
                })}
              </div>

              <p className="schedule-desc">{s.description}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
