"use client";

import { useEffect, useState, useCallback } from "react";
import {
  fetchMissions,
  createMission,
  approveMission,
  reviseMission,
  fetchTraces,
  fetchLineage,
} from "../../lib/api";
import { connectWebSocket } from "../../lib/api";

interface Mission {
  mission_id: string;
  status: string;
  phase: string;
  progress: number;
  cost_usd: number;
  success_count: number;
  total_count: number;
}

interface TraceEvent {
  iteration?: number;
  output?: string;
  passed?: boolean;
  score?: number;
  critique?: string;
  node_id?: string;
  output_preview?: string;
}

interface PromptVersion {
  version_id: string;
  template: string;
  change_log: string;
  stats?: Record<string, number>;
}

export default function MissionsPage() {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [traces, setTraces] = useState<TraceEvent[]>([]);
  const [lineage, setLineage] = useState<PromptVersion[]>([]);
  const [runfilePath, setRunfilePath] = useState("runfiles/test_mission.yaml");
  const [loading, setLoading] = useState(false);
  const [newPrompt, setNewPrompt] = useState("");
  const [changeLog, setChangeLog] = useState("");

  // Time-Travel Debugging state
  const [timeTravelIndex, setTimeTravelIndex] = useState(0);
  const maxIteration = traces.length;
  const timeTravelSnapshot = traces[timeTravelIndex] || null;

  const refresh = () => fetchMissions().then(setMissions).catch(console.error);

  // WebSocket for real-time updates
  const handleWsEvent = useCallback((event: Record<string, unknown>) => {
    const type = event.type as string;
    if (type === "TRACE_EVENT") {
      setTraces((prev) => [
        ...prev,
        {
          node_id: event.node_id as string,
          iteration: event.iteration as number,
          passed: event.passed as boolean,
          score: event.score as number,
          output_preview: event.output_preview as string,
        },
      ]);
    }
    if (type === "STATUS_UPDATE" || type === "COMPLETED" || type === "ERROR") {
      refresh();
    }
  }, []);

  useEffect(() => {
    connectWebSocket(handleWsEvent);
    refresh();
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, [handleWsEvent]);

  useEffect(() => {
    if (selectedId) {
      fetchTraces(selectedId).then(setTraces).catch(() => setTraces([]));
      fetchLineage(selectedId).then(setLineage).catch(() => setLineage([]));
    }
  }, [selectedId]);

  const handleCreate = async () => {
    setLoading(true);
    try {
      await createMission(runfilePath);
      setTimeout(refresh, 1000);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  };

  const handleApprove = async (id: string) => {
    await approveMission(id, "approve");
    refresh();
  };

  const handleAbort = async (id: string) => {
    await approveMission(id, "abort");
    refresh();
  };

  const handleRevise = async (id: string) => {
    if (!newPrompt.trim()) return;
    try {
      await reviseMission(id, newPrompt, changeLog);
      setNewPrompt("");
      setChangeLog("");
      // Refresh lineage to show new version
      setTimeout(() => {
        refresh();
        fetchLineage(id).then(setLineage).catch(() => {});
      }, 1000);
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="flex gap-6 h-full">
      {/* Main content */}
      <div className="flex-1 space-y-6">
        <h2 className="text-2xl font-bold">Missions</h2>

        {/* Create new mission */}
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <h3 className="font-semibold mb-3">New Mission</h3>
          <div className="flex gap-3">
            <input
              type="text"
              value={runfilePath}
              onChange={(e) => setRunfilePath(e.target.value)}
              className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm"
              placeholder="Runfile path"
            />
            <button
              onClick={handleCreate}
              disabled={loading}
              className="px-4 py-2 bg-[var(--color-accent)] text-white rounded text-sm disabled:opacity-50"
            >
              {loading ? "Starting..." : "Start Mission"}
            </button>
          </div>
        </div>

        {/* Sampling Decision Dashboard */}
        {missions.some((m) => m.phase === "AWAITING_APPROVAL") && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
            <h3 className="font-semibold text-amber-800 mb-3">
              ⏸️ Sampling Decision Required
            </h3>
            <div className="grid grid-cols-3 gap-4 text-sm">
              <div>
                <div className="text-amber-600 font-medium">Runfile Logic</div>
                <div className="text-xs text-gray-500 mt-1">
                  Review the task blueprint and prompt template
                </div>
              </div>
              <div>
                <div className="text-amber-600 font-medium">Sample Results</div>
                <div className="text-xs text-gray-500 mt-1">
                  Preview the 1% sampling output with Critic evaluations
                </div>
              </div>
              <div>
                <div className="text-amber-600 font-medium">Economics</div>
                <div className="text-xs text-gray-500 mt-1">
                  Cost estimate, duration, and ROI projection
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Mission list */}
        <div className="space-y-3">
          {missions.map((m) => (
            <div
              key={m.mission_id}
              className={`bg-white border rounded-lg p-4 cursor-pointer transition-colors ${
                selectedId === m.mission_id
                  ? "border-[var(--color-accent)] ring-1 ring-[var(--color-accent)]"
                  : "border-gray-200 hover:border-gray-300"
              }`}
              onClick={() => setSelectedId(m.mission_id)}
            >
              <div className="flex items-center justify-between">
                <div>
                  <span className="font-mono text-sm">{m.mission_id}</span>
                  <span className="ml-3 text-xs px-2 py-1 rounded bg-gray-100">
                    {m.status}
                  </span>
                  <span className="ml-2 text-xs text-gray-500">{m.phase}</span>
                </div>
                <div className="flex gap-2">
                  {m.status === "awaiting_approval" && (
                    <>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleApprove(m.mission_id);
                        }}
                        className="px-3 py-1 bg-green-500 text-white rounded text-xs"
                      >
                        Approve & Run
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleAbort(m.mission_id);
                        }}
                        className="px-3 py-1 bg-red-500 text-white rounded text-xs"
                      >
                        Abort
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="mt-2 flex gap-6 text-xs text-gray-500">
                <span>Cost: ${m.cost_usd.toFixed(4)}</span>
                <span>
                  Success: {m.success_count}/{m.total_count}
                </span>
              </div>
              {m.progress > 0 && (
                <div className="mt-2 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-[var(--color-accent)] rounded-full transition-all"
                    style={{ width: `${m.progress * 100}%` }}
                  />
                </div>
              )}
            </div>
          ))}
          {missions.length === 0 && (
            <div className="text-center text-gray-400 py-8">No missions yet.</div>
          )}
        </div>

        {/* Live Trace View — real-time WebSocket updates + Time-Travel */}
        {selectedId && (
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <h3 className="font-semibold mb-3">
              Live Trace — {selectedId}
              <span className="ml-2 text-xs text-green-600 animate-pulse">● Live</span>
            </h3>

            {/* Time-Travel Debugging Slider */}
            {traces.length > 1 && (
              <div className="mb-4 p-3 bg-gray-50 rounded border">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-gray-600">
                    Time-Travel Debugging
                  </span>
                  <span className="text-xs text-gray-500">
                    Iteration {timeTravelIndex + 1} / {maxIteration}
                  </span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={maxIteration - 1}
                  value={timeTravelIndex}
                  onChange={(e) => setTimeTravelIndex(Number(e.target.value))}
                  className="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer"
                />
                <div className="flex justify-between text-xs text-gray-400 mt-1">
                  <span>First</span>
                  <span>Latest</span>
                </div>
                {/* Snapshot at current iteration */}
                {timeTravelSnapshot && (
                  <div className="mt-3 p-2 bg-white rounded border text-xs">
                    <div className="flex justify-between">
                      <span className="font-semibold">
                        Iteration {timeTravelSnapshot.iteration}
                      </span>
                      <span className={timeTravelSnapshot.passed ? "text-green-600" : "text-red-600"}>
                        {timeTravelSnapshot.passed ? "✓ Passed" : "✗ Failed"}
                        {timeTravelSnapshot.score !== undefined &&
                          ` (Score: ${timeTravelSnapshot.score.toFixed(2)})`}
                      </span>
                    </div>
                    {timeTravelSnapshot.output_preview && (
                      <div className="mt-2 p-2 bg-gray-50 rounded font-mono text-gray-700">
                        {timeTravelSnapshot.output_preview}
                      </div>
                    )}
                    {timeTravelSnapshot.critique && (
                      <div className="mt-1 text-red-600">
                        Critique: {timeTravelSnapshot.critique}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {traces.length === 0 ? (
              <div className="text-center text-gray-400 py-4 text-sm">
                Waiting for execution traces...
              </div>
            ) : (
              <div className="space-y-2 max-h-64 overflow-auto">
                {traces.map((t, i) => (
                  <div
                    key={i}
                    className={`text-xs p-2 rounded ${
                      t.passed
                        ? "bg-green-50 border border-green-200"
                        : "bg-red-50 border border-red-200"
                    }`}
                  >
                    <div className="flex justify-between">
                      <span>
                        {t.node_id && `${t.node_id} — `}
                        Iteration {t.iteration}
                      </span>
                      <span>Score: {t.score?.toFixed(2)}</span>
                    </div>
                    {t.output_preview && (
                      <div className="mt-1 text-gray-600 truncate">
                        {t.output_preview.substring(0, 120)}...
                      </div>
                    )}
                    {t.critique && (
                      <div className="mt-1 text-red-600">{t.critique}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Right sidebar — Prompt Editor + Lineage */}
      <aside className="w-80 bg-white border border-gray-200 rounded-lg p-4 flex flex-col">
        <h3 className="font-semibold mb-3">Prompt Editor</h3>
        <p className="text-xs text-gray-500 mb-3">
          Edit the prompt and click &quot;Edit &amp; Resample&quot; to create a new
          version in the Prompt Lineage.
        </p>

        <textarea
          value={newPrompt}
          onChange={(e) => setNewPrompt(e.target.value)}
          className="flex-1 border border-gray-300 rounded p-2 text-sm font-mono resize-none"
          placeholder="Enter new prompt template..."
          rows={8}
        />

        <input
          type="text"
          value={changeLog}
          onChange={(e) => setChangeLog(e.target.value)}
          className="mt-2 border border-gray-300 rounded px-2 py-1 text-xs"
          placeholder="Change log (why you're modifying)..."
        />

        <div className="mt-3 flex gap-2">
          <button
            onClick={() => selectedId && handleRevise(selectedId)}
            disabled={!selectedId || !newPrompt.trim()}
            className="flex-1 px-3 py-2 bg-amber-500 text-white rounded text-sm disabled:opacity-50"
          >
            Edit & Resample
          </button>
          <button
            onClick={() => {
              setNewPrompt("");
              setChangeLog("");
            }}
            className="px-3 py-2 border border-gray-300 rounded text-sm"
          >
            Clear
          </button>
        </div>

        {/* Prompt Lineage Display */}
        <div className="mt-4 border-t pt-3">
          <h4 className="text-xs font-semibold text-gray-600 mb-2">
            Prompt Lineage
          </h4>
          {lineage.length === 0 ? (
            <div className="text-xs text-gray-400">
              No versions yet. Start a mission to create v1.0.
            </div>
          ) : (
            <div className="space-y-2">
              {lineage.map((v, i) => (
                <div
                  key={v.version_id}
                  className="text-xs p-2 bg-gray-50 rounded border"
                >
                  <div className="flex justify-between items-center">
                    <span className="font-mono font-semibold">
                      {v.version_id}
                    </span>
                    {i === lineage.length - 1 && (
                      <span className="text-xs px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded">
                        Current
                      </span>
                    )}
                  </div>
                  {v.change_log && (
                    <div className="text-gray-500 mt-1">{v.change_log}</div>
                  )}
                  {v.stats && Object.keys(v.stats).length > 0 && (
                    <div className="mt-1 flex gap-2">
                      {v.stats.pass_rate !== undefined && (
                        <span className="text-green-600">
                          Pass: {(v.stats.pass_rate * 100).toFixed(0)}%
                        </span>
                      )}
                      {v.stats.total_cost !== undefined && (
                        <span className="text-gray-500">
                          Cost: ${v.stats.total_cost.toFixed(4)}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              ))}

              {/* Version comparison */}
              {lineage.length >= 2 && (
                <div className="mt-2 p-2 bg-blue-50 rounded border border-blue-200">
                  <div className="text-xs font-semibold text-blue-700 mb-1">
                    Version Comparison
                  </div>
                  <div className="text-xs text-gray-600">
                    {lineage[0]?.version_id} → {lineage[lineage.length - 1]?.version_id}
                    {lineage[0]?.stats?.pass_rate !== undefined &&
                      lineage[lineage.length - 1]?.stats?.pass_rate !== undefined && (
                        <span className="ml-2">
                          Pass rate:{" "}
                          {((lineage[0]!.stats!.pass_rate as number) * 100).toFixed(0)}% →{" "}
                          {((lineage[lineage.length - 1]!.stats!.pass_rate as number) * 100).toFixed(0)}%
                        </span>
                      )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
