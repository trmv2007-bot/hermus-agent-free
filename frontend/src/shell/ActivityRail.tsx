// What the room is actually doing, right now.
//
// This exists because the room was collecting real signal and displaying none
// of it. Eight kinds of runtime event push into the workspace `tray`; the core
// read it to pick a colour and nothing ever drew it. So the room looked exactly
// as alive as a static screenshot — a grid, a glow, and a word.
//
// Everything here is already-real data. The feed is the event stream the
// workspace has always received; the stats are read from the endpoints that are
// already open. Nothing on this rail is decoration, and if the gateway goes
// away the rail says so rather than going quiet.
//
// It is deliberately quiet: a rail of monospace lines at the edge of the room,
// not a dashboard. Its job is to make the room legible when you glance at it,
// which is the difference between a tool that is working and a project that
// renders.

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useWorkspace } from "../state/workspace-store";
import { api } from "../api/client";

interface Stat {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "muted";
}

function ago(at: number): string {
  const seconds = Math.max(0, Math.round((Date.now() - at) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

export function ActivityRail() {
  const tray = useWorkspace((state) => state.tray);
  // The feed shows relative times, so it must re-render for them to stay honest.
  // One interval for the whole rail rather than one per line.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, []);

  // Real readouts, from endpoints that already exist. Polled slowly because
  // they describe state that changes on the order of minutes.
  const status = useQuery({ queryKey: ["rail-engine"], queryFn: () => api.engineStatus(), refetchInterval: 15000 });
  const graph = useQuery({ queryKey: ["rail-memory"], queryFn: () => api.memoryGraph(400), refetchInterval: 30000 });

  const roles = Object.values(status.data?.plan?.roles ?? {});
  const primary = roles.find((role) => role.model)?.model;

  const stats: Stat[] = [
    { label: "model", value: primary ?? (status.isError ? "unreachable" : "—"), tone: primary ? "ok" : "muted" },
    { label: "memories", value: graph.data ? `${graph.data.nodes.length}` : "—", tone: "muted" },
    {
      label: "links",
      value: graph.data ? (graph.data.edges_are_inferred ? `${graph.data.edges.length} inferred` : `${graph.data.edges.length}`) : "—",
      tone: "muted",
    },
  ];

  const recent = tray.slice(0, 7);

  return (
    <aside className="rail" aria-label="what the room is doing">
      <div className="rail-stats">
        {stats.map((stat) => (
          <span className="rail-stat" key={stat.label} data-tone={stat.tone ?? "muted"}>
            <em>{stat.label}</em>
            <b>{stat.value}</b>
          </span>
        ))}
      </div>

      {/*
        The feed is the event stream itself. When it is empty the room says so
        plainly rather than showing a placeholder that implies work is coming —
        an empty room that claims to be busy is worse than an empty room.
      */}
      {recent.length ? (
        <ul className="rail-feed">
          {recent.map((entry, index) => (
            <li key={`${entry.at}-${index}`}>
              <span className="rail-when">{ago(entry.at)}</span>
              <span className="rail-what">{entry.label}</span>
              {entry.detail ? <span className="rail-detail">{entry.detail}</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="rail-idle">nothing has happened yet</p>
      )}
    </aside>
  );
}
