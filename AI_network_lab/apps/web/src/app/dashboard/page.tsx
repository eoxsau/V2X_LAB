"use client";

import type { ReactNode } from "react";
import { MapContainer } from "@/components/map/MapContainer";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

const sidebarItems = [
  "Dashboard",
  "Scenarios",
  "Experiments",
  "Base Stations",
  "Simulator",
  "API Docs",
  "Settings"
];

const latencyData = [
  { time: "00:00", latency: 18, congestion: 22 },
  { time: "00:10", latency: 21, congestion: 26 },
  { time: "00:20", latency: 17, congestion: 20 },
  { time: "00:30", latency: 29, congestion: 38 },
  { time: "00:40", latency: 24, congestion: 34 },
  { time: "00:50", latency: 35, congestion: 52 },
  { time: "01:00", latency: 31, congestion: 47 }
];

const eventLog = [
  { time: "12:44:02", type: "INFO", text: "Scenario sandbox initialized" },
  { time: "12:44:18", type: "SYNC", text: "Telemetry mock stream hydrated" },
  { time: "12:45:01", type: "WARN", text: "Sector C congestion crossed threshold" },
  { time: "12:46:20", type: "OK", text: "Base station BS-03 handoff queue normalized" }
];

const statusRows = [
  ["Node", "BS-03"],
  ["Band", "n78"],
  ["Load", "71%"],
  ["Users", "1,284"]
];

function Panel({
  children,
  className = ""
}: Readonly<{
  children: ReactNode;
  className?: string;
}>) {
  return (
    <section
      className={`rounded-md border border-[#1F2937] bg-[#111827] shadow-[0_18px_60px_rgba(0,0,0,0.35)] ${className}`}
    >
      {children}
    </section>
  );
}

function PanelHeader({
  eyebrow,
  title,
  action
}: Readonly<{
  eyebrow: string;
  title: string;
  action?: string;
}>) {
  return (
    <div className="flex items-center justify-between border-b border-[#1F2937] px-5 py-4">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-cyan-300">
          {eyebrow}
        </p>
        <h2 className="mt-1 text-base font-semibold text-slate-100">{title}</h2>
      </div>
      {action ? (
        <span className="rounded-sm border border-cyan-400/30 bg-cyan-400/10 px-2 py-1 text-xs font-medium text-cyan-200">
          {action}
        </span>
      ) : null}
    </div>
  );
}

export default function DashboardPage() {
  return (
    <main className="min-h-screen bg-[#050816] text-slate-100">
      <div className="grid min-h-screen grid-cols-1 gap-3 p-3 lg:grid-cols-[260px_minmax(0,1fr)_340px] lg:grid-rows-[minmax(0,1fr)_260px]">
        <aside className="rounded-md border border-[#1F2937] bg-[#0b1020] p-4 lg:row-span-2">
          <div className="border-b border-[#1F2937] pb-5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-cyan-300">
              Network Lab
            </p>
            <h1 className="mt-3 text-2xl font-semibold leading-tight text-white">
              Command Center
            </h1>
            <p className="mt-2 text-sm leading-6 text-slate-400">
              Developer console for digital twin experiments.
            </p>
          </div>

          <nav className="mt-5 space-y-1">
            {sidebarItems.map((item) => {
              const active = item === "Dashboard";
              return (
                <button
                  className={`flex w-full items-center justify-between rounded-md px-3 py-2.5 text-left text-sm transition ${
                    active
                      ? "border border-cyan-400/40 bg-cyan-400/10 text-cyan-100"
                      : "text-slate-400 hover:bg-white/5 hover:text-slate-100"
                  }`}
                  key={item}
                  type="button"
                >
                  <span>{item}</span>
                  {active ? <span className="h-1.5 w-1.5 rounded-full bg-cyan-300" /> : null}
                </button>
              );
            })}
          </nav>

          <div className="mt-8 rounded-md border border-[#1F2937] bg-[#050816] p-4">
            <p className="text-xs font-medium uppercase tracking-[0.16em] text-slate-500">
              System
            </p>
            <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
              <div>
                <p className="text-slate-500">Mode</p>
                <p className="font-semibold text-emerald-300">Mock</p>
              </div>
              <div>
                <p className="text-slate-500">API</p>
                <p className="font-semibold text-amber-300">Idle</p>
              </div>
            </div>
          </div>
        </aside>

        <Panel className="min-h-[560px] overflow-hidden">
          <PanelHeader
            eyebrow="Simulation Map"
            title="VWorld Korean Public Spatial Twin"
            action="Mock Data"
          />
          <MapContainer />
        </Panel>

        <Panel className="overflow-hidden">
          <PanelHeader eyebrow="Network Status" title="Selection Inspector" />
          <div className="space-y-4 p-5">
            <div className="rounded-md border border-[#1F2937] bg-[#050816] p-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-slate-100">Selected Base Station</h3>
                <span className="text-xs text-emerald-300">Online</span>
              </div>
              <div className="mt-4 grid grid-cols-2 gap-3">
                {statusRows.map(([label, value]) => (
                  <div key={label}>
                    <p className="text-xs uppercase tracking-[0.14em] text-slate-500">
                      {label}
                    </p>
                    <p className="mt-1 font-mono text-sm text-slate-200">{value}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-md border border-[#1F2937] bg-[#050816] p-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-slate-100">Selected User</h3>
                <span className="text-xs text-cyan-300">UE-1842</span>
              </div>
              <div className="mt-4 space-y-3 text-sm">
                <div className="flex justify-between text-slate-400">
                  <span>Signal</span>
                  <span className="font-mono text-slate-100">-84 dBm</span>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Throughput</span>
                  <span className="font-mono text-slate-100">142 Mbps</span>
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>Handoff Risk</span>
                  <span className="font-mono text-amber-300">Medium</span>
                </div>
              </div>
            </div>

            <div className="rounded-md border border-[#1F2937] bg-[#050816] p-4">
              <h3 className="font-semibold text-slate-100">Simulation Status</h3>
              <div className="mt-4 h-2 rounded-full bg-slate-800">
                <div className="h-2 w-[68%] rounded-full bg-cyan-400" />
              </div>
              <div className="mt-4 flex justify-between text-sm text-slate-400">
                <span>Clock</span>
                <span className="font-mono text-slate-100">00:47:12</span>
              </div>
              <div className="mt-2 flex justify-between text-sm text-slate-400">
                <span>State</span>
                <span className="font-mono text-emerald-300">Running</span>
              </div>
            </div>
          </div>
        </Panel>

        <Panel className="overflow-hidden lg:col-span-2">
          <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_420px]">
            <div className="border-b border-[#1F2937] lg:border-b-0 lg:border-r">
              <PanelHeader eyebrow="Metrics" title="Latency and Congestion Placeholder" />
              <div className="h-[180px] px-4 py-3">
                <ResponsiveContainer height="100%" width="100%">
                  <AreaChart data={latencyData}>
                    <defs>
                      <linearGradient id="latency" x1="0" x2="0" y1="0" y2="1">
                        <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.45} />
                        <stop offset="95%" stopColor="#22d3ee" stopOpacity={0} />
                      </linearGradient>
                      <linearGradient id="congestion" x1="0" x2="0" y1="0" y2="1">
                        <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.35} />
                        <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="rgba(148, 163, 184, 0.12)" vertical={false} />
                    <XAxis
                      axisLine={false}
                      dataKey="time"
                      tick={{ fill: "#94a3b8", fontSize: 12 }}
                      tickLine={false}
                    />
                    <YAxis
                      axisLine={false}
                      tick={{ fill: "#94a3b8", fontSize: 12 }}
                      tickLine={false}
                      width={36}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "#050816",
                        border: "1px solid #1F2937",
                        borderRadius: 6,
                        color: "#e5edf7"
                      }}
                    />
                    <Area
                      dataKey="latency"
                      fill="url(#latency)"
                      name="Latency"
                      stroke="#22d3ee"
                      strokeWidth={2}
                      type="monotone"
                    />
                    <Area
                      dataKey="congestion"
                      fill="url(#congestion)"
                      name="Congestion"
                      stroke="#f59e0b"
                      strokeWidth={2}
                      type="monotone"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div>
              <PanelHeader eyebrow="Events" title="Developer Console Log" />
              <div className="h-[180px] space-y-3 overflow-hidden p-4 font-mono text-xs">
                {eventLog.map((event) => (
                  <div className="grid grid-cols-[72px_44px_1fr] gap-3" key={event.time}>
                    <span className="text-slate-500">{event.time}</span>
                    <span
                      className={
                        event.type === "WARN"
                          ? "text-amber-300"
                          : event.type === "OK"
                            ? "text-emerald-300"
                            : "text-cyan-300"
                      }
                    >
                      {event.type}
                    </span>
                    <span className="text-slate-300">{event.text}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Panel>
      </div>
    </main>
  );
}
