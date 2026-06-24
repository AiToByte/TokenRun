"use client";

import { useEffect, useState, useCallback } from "react";
import { connectWebSocket, type EventHandler } from "./api";

export interface TelemetryEvent {
  type: string;
  mission_id?: string;
  phase?: string;
  progress?: number;
  cost_usd?: number;
  data?: Record<string, unknown>;
}

/**
 * Hook to subscribe to real-time telemetry events via WebSocket.
 */
export function useTelemetry() {
  const [events, setEvents] = useState<TelemetryEvent[]>([]);
  const [connected, setConnected] = useState(false);

  const handler: EventHandler = useCallback((event) => {
    setEvents((prev) => [...prev.slice(-99), event]); // keep last 100
  }, []);

  useEffect(() => {
    connectWebSocket(handler);
    setConnected(true);
    return () => {
      setConnected(false);
    };
  }, [handler]);

  return { events, connected };
}
