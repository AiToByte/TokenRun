"use client";

import { useEffect, useState } from "react";
import {
  fetchMissions,
  createMission,
  approveMission,
  fetchTraces,
} from "@/lib/api";

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
}

export default function MissionsPage() {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [traces, setTraces] = useState<TraceEvent[]>([]);
  const [runfilePath, setRunfilePath] = useState("runfiles/test_mission.yaml");
  const [loading, setLoading] = useState(false);
  const [newPrompt, setNewPrompt] = useState("");

  const refresh = () => fetchMissions().then(setMissions).catch(console.error);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (selectedId) {
      fetchTraces(selectedId).then(setTraces).catch(console.error);
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
    await approveMission(id, "revise", newPrompt);
    setNewPrompt("");
    refresh();
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

        {/* Sampling Decision Dashboard (#9) */}
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

        {/* Live Trace View */}
        {selectedId && traces.length > 0 && (
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <h3 className="font-semibold mb-3">
              Live Trace — {selectedId}
            </h3>
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
                    <span>Iteration {t.iteration}</span>
                    <span>Score: {t.score?.toFixed(2)}</span>
                  </div>
                  {t.output && (
                    <div className="mt-1 text-gray-600 truncate">
                      {t.output.substring(0, 100)}...
                    </div>
                  )}
                  {t.critique && (
                    <div className="mt-1 text-red-600">{t.critique}</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Right sidebar — Prompt Editor (#10) */}
      <aside className="w-80 bg-white border border-gray-200 rounded-lg p-4 flex flex-col">
        <h3 className="font-semibold mb-3">Prompt Editor</h3>
        <p className="text-xs text-gray-500 mb-3">
          Edit the prompt template and trigger Edit & Resample to create a new
          version in the Prompt Lineage.
        </p>
        <textarea
          value={newPrompt}
          onChange={(e) => setNewPrompt(e.target.value)}
          className="flex-1 border border-gray-300 rounded p-2 text-sm font-mono resize-none"
          placeholder="Enter new prompt template..."
          rows={12}
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
            onClick={() => setNewPrompt("")}
            className="px-3 py-2 border border-gray-300 rounded text-sm"
          >
            Clear
          </button>
        </div>
        <div className="mt-4 text-xs text-gray-400">
          <div className="font-medium text-gray-600 mb-1">Prompt Lineage</div>
          <div>v1.0 — Initial version</div>
          <div className="text-gray-300">Click Edit & Resample to create v1.1</div>
        </div>
      </aside>
    </div>
  );
}
