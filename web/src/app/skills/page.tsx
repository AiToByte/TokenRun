"use client";

import { useEffect, useState } from "react";
import { fetchSkills } from "@/lib/api";

interface Skill {
  skill_id: string;
  name: string;
  created_at: string;
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);

  useEffect(() => {
    fetchSkills().then(setSkills).catch(console.error);
  }, []);

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">Solidified Skills</h2>
      <p className="text-sm text-gray-500">
        Skills are reusable task blueprints extracted from successful mission
        runs. They lock the optimal prompt, model config, and validation rules.
      </p>

      <div className="grid grid-cols-2 gap-4">
        {skills.length === 0 ? (
          <div className="col-span-2 text-center text-gray-400 py-12">
            No skills solidified yet. Complete a mission to create your first
            skill.
          </div>
        ) : (
          skills.map((s) => (
            <div
              key={s.skill_id}
              className="bg-white border border-gray-200 rounded-lg p-5 hover:border-[var(--color-accent)] transition-colors"
            >
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="font-semibold">{s.name}</h3>
                  <div className="text-xs font-mono text-gray-500 mt-1">
                    {s.skill_id}
                  </div>
                </div>
                <span className="text-xs px-2 py-1 bg-green-100 text-green-700 rounded">
                  Active
                </span>
              </div>
              <div className="text-xs text-gray-400 mt-3">
                Created: {s.created_at}
              </div>
              <div className="mt-4 flex gap-2">
                <button className="px-3 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50">
                  View Details
                </button>
                <button className="px-3 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50">
                  Run Again
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
