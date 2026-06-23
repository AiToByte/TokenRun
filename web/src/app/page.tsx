"use client";

import { useEffect, useState } from "react";
import { fetchMissions, fetchSkills } from "@/lib/api";

interface Mission {
  mission_id: string;
  status: string;
  phase: string;
  progress: number;
  cost_usd: number;
  success_count: number;
  total_count: number;
}

interface Skill {
  skill_id: string;
  name: string;
  created_at: string;
}

export default function Dashboard() {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);

  useEffect(() => {
    fetchMissions().then(setMissions).catch(console.error);
    fetchSkills().then(setSkills).catch(console.error);
  }, []);

  return (
    <div className="space-y-8">
      <h2 className="text-2xl font-bold">Dashboard</h2>

      {/* Stats cards */}
      <div className="grid grid-cols-4 gap-4">
        <StatCard label="Active Missions" value={missions.length.toString()} />
        <StatCard
          label="Total Cost"
          value={`$${missions.reduce((s, m) => s + m.cost_usd, 0).toFixed(2)}`}
        />
        <StatCard label="Skills Saved" value={skills.length.toString()} />
        <StatCard label="API Status" value="Healthy" variant="success" />
      </div>

      {/* Recent missions */}
      <section>
        <h3 className="text-lg font-semibold mb-3">Recent Missions</h3>
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="px-4 py-2">ID</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Phase</th>
                <th className="px-4 py-2">Cost</th>
                <th className="px-4 py-2">Success</th>
              </tr>
            </thead>
            <tbody>
              {missions.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-gray-400">
                    No missions yet. Submit a Runfile to get started.
                  </td>
                </tr>
              ) : (
                missions.map((m) => (
                  <tr key={m.mission_id} className="border-t">
                    <td className="px-4 py-2 font-mono text-xs">{m.mission_id}</td>
                    <td className="px-4 py-2">
                      <StatusBadge status={m.status} />
                    </td>
                    <td className="px-4 py-2">{m.phase}</td>
                    <td className="px-4 py-2">${m.cost_usd.toFixed(4)}</td>
                    <td className="px-4 py-2">
                      {m.success_count}/{m.total_count}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Skills */}
      <section>
        <h3 className="text-lg font-semibold mb-3">Solidified Skills</h3>
        <div className="grid grid-cols-3 gap-4">
          {skills.length === 0 ? (
            <div className="col-span-3 text-center text-gray-400 py-8">
              No skills solidified yet.
            </div>
          ) : (
            skills.map((s) => (
              <div
                key={s.skill_id}
                className="bg-white border border-gray-200 rounded-lg p-4"
              >
                <div className="font-semibold text-sm">{s.name}</div>
                <div className="text-xs text-gray-500 font-mono mt-1">
                  {s.skill_id}
                </div>
                <div className="text-xs text-gray-400 mt-2">{s.created_at}</div>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}

function StatCard({
  label,
  value,
  variant = "default",
}: {
  label: string;
  value: string;
  variant?: "default" | "success" | "danger";
}) {
  const colors = {
    default: "border-gray-200",
    success: "border-green-300 bg-green-50",
    danger: "border-red-300 bg-red-50",
  };
  return (
    <div className={`bg-white border rounded-lg p-4 ${colors[variant]}`}>
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-2xl font-bold mt-1">{value}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pending: "bg-gray-100 text-gray-700",
    running: "bg-blue-100 text-blue-700",
    completed: "bg-green-100 text-green-700",
    failed: "bg-red-100 text-red-700",
  };
  return (
    <span className={`text-xs px-2 py-1 rounded ${colors[status] || colors.pending}`}>
      {status}
    </span>
  );
}
