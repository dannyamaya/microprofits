import { useState, useEffect } from "react";

interface StrategySchedule {
  name: string;
  epic: string;
  account: string;
  utcStart: number;
  utcEnd: number;
  description: string;
  phases: { label: string; utcStart: number; utcEnd: number }[];
}

const STRATEGIES: StrategySchedule[] = [
  {
    name: "Scalper",
    epic: "US100",
    account: "microprofits",
    utcStart: 14,
    utcEnd: 21,
    description: "Momentum scalping during US market hours",
    phases: [
      { label: "Active", utcStart: 14, utcEnd: 21 },
    ],
  },
  {
    name: "Asian Range",
    epic: "GOLD",
    account: "asian_range",
    utcStart: 0,
    utcEnd: 12,
    description: "Asian session range breakout at London open",
    phases: [
      { label: "Building range", utcStart: 0, utcEnd: 7 },
      { label: "Breakout window", utcStart: 8, utcEnd: 12 },
    ],
  },
];

const COL_OFFSET = -5; // Colombia = UTC-5

function utcToColombia(hour: number): number {
  return ((hour + COL_OFFSET) % 24 + 24) % 24;
}

function fmtHour(h: number): string {
  const suffix = h >= 12 ? "PM" : "AM";
  const display = h === 0 ? 12 : h > 12 ? h - 12 : h;
  return `${display}${suffix}`;
}

function getCurrentPhase(strategy: StrategySchedule, utcHour: number): string | null {
  for (const phase of strategy.phases) {
    if (phase.utcStart <= phase.utcEnd) {
      if (utcHour >= phase.utcStart && utcHour < phase.utcEnd) return phase.label;
    } else {
      // Wraps midnight
      if (utcHour >= phase.utcStart || utcHour < phase.utcEnd) return phase.label;
    }
  }
  return null;
}

function isInWindow(utcHour: number, start: number, end: number): boolean {
  if (start <= end) return utcHour >= start && utcHour < end;
  return utcHour >= start || utcHour < end;
}

export default function SchedulePanel() {
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
          const active = isInWindow(utcHour, s.utcStart, s.utcEnd);
          const phase = getCurrentPhase(s, utcHour);

          return (
            <div key={s.epic} className={`schedule-card ${active ? "schedule-active" : "schedule-inactive"}`}>
              <div className="schedule-header">
                <div className="schedule-title">
                  <span className={`schedule-dot ${active ? "dot-on" : "dot-off"}`} />
                  <strong>{s.name}</strong>
                  <span className="schedule-epic">{s.epic}</span>
                </div>
                <span className="schedule-account">{s.account}</span>
              </div>

              {phase && (
                <div className="schedule-phase">
                  {phase}
                </div>
              )}

              <div className="schedule-times">
                {s.phases.map((p) => (
                  <div key={p.label} className="schedule-time-row">
                    <span className="schedule-phase-label">{p.label}</span>
                    <span className="schedule-hours">
                      {fmtHour(utcToColombia(p.utcStart))}–{fmtHour(utcToColombia(p.utcEnd))} COL
                    </span>
                    <span className="schedule-hours-utc">
                      {fmtHour(p.utcStart)}–{fmtHour(p.utcEnd)} UTC
                    </span>
                  </div>
                ))}
              </div>

              <p className="schedule-desc">{s.description}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
