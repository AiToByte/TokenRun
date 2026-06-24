/**
 * TokenRun Cockpit API Client
 *
 * Connects to the FastAPI backend via REST and WebSocket.
 * Uses Next.js rewrites to proxy /api/* → http://localhost:8000/*
 */

const API_BASE = "/api";

// ---------------------------------------------------------------------------
// REST API
// ---------------------------------------------------------------------------

export async function fetchMissions() {
  const resp = await fetch(`${API_BASE}/missions`);
  if (!resp.ok) throw new Error(`Failed to fetch missions: ${resp.status}`);
  return resp.json();
}

export async function fetchMission(missionId: string) {
  const resp = await fetch(`${API_BASE}/missions/${missionId}`);
  if (!resp.ok) throw new Error(`Failed to fetch mission: ${resp.status}`);
  return resp.json();
}

export async function createMission(runfilePath: string, sampleOnly = false) {
  const resp = await fetch(`${API_BASE}/missions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      runfile_path: runfilePath,
      sample_only: sampleOnly,
    }),
  });
  if (!resp.ok) throw new Error(`Failed to create mission: ${resp.status}`);
  return resp.json();
}

export async function approveMission(
  missionId: string,
  action: "approve" | "revise" | "abort",
  newPrompt?: string
) {
  const resp = await fetch(`${API_BASE}/missions/${missionId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, new_prompt: newPrompt }),
  });
  if (!resp.ok) throw new Error(`Failed to approve mission: ${resp.status}`);
  return resp.json();
}

export async function reviseMission(
  missionId: string,
  newPrompt: string,
  changeLog = ""
) {
  const resp = await fetch(`${API_BASE}/missions/${missionId}/revise`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_prompt: newPrompt, change_log: changeLog }),
  });
  if (!resp.ok) throw new Error(`Failed to revise mission: ${resp.status}`);
  return resp.json();
}

export async function fetchTraces(missionId: string) {
  const resp = await fetch(`${API_BASE}/missions/${missionId}/traces`);
  if (!resp.ok) throw new Error(`Failed to fetch traces: ${resp.status}`);
  return resp.json();
}

export async function fetchLineage(missionId: string) {
  const resp = await fetch(`${API_BASE}/missions/${missionId}/lineage`);
  if (!resp.ok) throw new Error(`Failed to fetch lineage: ${resp.status}`);
  return resp.json();
}

export async function fetchSkills() {
  const resp = await fetch(`${API_BASE}/skills`);
  if (!resp.ok) throw new Error(`Failed to fetch skills: ${resp.status}`);
  return resp.json();
}

export async function runSkill(skillId: string) {
  const resp = await fetch(`${API_BASE}/skills/${skillId}/run`, {
    method: "POST",
  });
  if (!resp.ok) throw new Error(`Failed to run skill: ${resp.status}`);
  return resp.json();
}

export async function healthCheck() {
  const resp = await fetch(`${API_BASE}/health`);
  return resp.json();
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

export type EventHandler = (event: {
  type: string;
  mission_id?: string;
  [key: string]: unknown;
}) => void;

let _ws: WebSocket | null = null;
const _handlers: EventHandler[] = [];

export function connectWebSocket(onEvent?: EventHandler) {
  if (onEvent) _handlers.push(onEvent);

  if (_ws?.readyState === WebSocket.OPEN) return;

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws`;
  _ws = new WebSocket(wsUrl);

  _ws.onmessage = (msg) => {
    try {
      const data = JSON.parse(msg.data);
      _handlers.forEach((h) => h(data));
    } catch {
      // ignore non-JSON messages
    }
  };

  _ws.onclose = () => {
    // Auto-reconnect after 3s
    setTimeout(() => connectWebSocket(), 3000);
  };
}

export function disconnectWebSocket() {
  _ws?.close();
  _ws = null;
}
