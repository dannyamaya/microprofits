const BASE = "";

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  return res.json();
}

// Status
export const getStatus = () => request<any>("/api/status");
export const startBot = () =>
  request<any>("/api/bot/start", { method: "POST" });
export const stopBot = () =>
  request<any>("/api/bot/stop", { method: "POST" });
export const flattenAll = () =>
  request<any>("/api/bot/flatten", { method: "POST" });

// Config
export const getConfig = () => request<any>("/api/config");
export const updateConfig = (data: Record<string, any>) =>
  request<any>("/api/config", {
    method: "PUT",
    body: JSON.stringify(data),
  });

// Symbols
export const getSymbols = () => request<any[]>("/api/config/symbols");
export const updateSymbol = (epic: string, data: Record<string, any>) =>
  request<any>(`/api/config/symbols/${epic}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });

// Positions
export const getPositions = () => request<any[]>("/api/positions");

// Trades
export const getTrades = (limit = 50, offset = 0) =>
  request<any[]>(`/api/trades?limit=${limit}&offset=${offset}`);
export const getStats = () => request<any>("/api/trades/stats");

// Heatmap
export const getHeatmap = () => request<any[]>("/api/heatmap");

// Audit
export const getAudit = (limit = 100) =>
  request<any[]>(`/api/audit?limit=${limit}`);

// Bias Dashboard
export const getBiasConfig = () => request<any>("/api/bias/config");
export const updateBiasConfig = (data: Record<string, any>) =>
  request<any>("/api/bias/config", {
    method: "PUT",
    body: JSON.stringify(data),
  });
export const getBiasInstruments = () => request<any[]>("/api/bias/instruments");
export const updateBiasInstrument = (epic: string, data: Record<string, any>) =>
  request<any>(`/api/bias/instruments/${epic}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
export const getBiasPositions = () => request<any[]>("/api/bias/positions");
export const getBiasTrades = (limit = 50) =>
  request<any[]>(`/api/bias/trades?limit=${limit}`);
export const getBiasStats = () => request<any>("/api/bias/trades/stats");
export const getBiasAudit = (limit = 100) =>
  request<any[]>(`/api/bias/audit?limit=${limit}`);
export const getBiasAccount = () => request<any>("/api/bias/account");
export const getBiasSignals = () => request<any>("/api/bias");
export const getHeadlines = (limit = 60) =>
  request<any[]>(`/api/headlines?limit=${limit}`);
