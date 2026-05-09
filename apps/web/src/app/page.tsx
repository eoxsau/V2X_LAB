import Link from "next/link";

export default function Home() {
  return (
    <main className="grid min-h-screen place-items-center bg-[#050816] px-6 text-slate-100">
      <section className="max-w-2xl">
        <p className="text-xs font-semibold uppercase tracking-[0.22em] text-cyan-300">
          Autonomous V2X AI Routing Lab
        </p>
        <h1 className="mt-4 text-5xl font-semibold tracking-normal text-white">
          Network-aware routing for autonomous V2X simulation.
        </h1>
        <p className="mt-5 text-lg leading-8 text-slate-400">
          Compare rule-based baseline routing with AI-assisted optimization using
          distance, congestion, latency, base-station load, and edge proximity.
        </p>
        <Link
          className="mt-8 inline-flex rounded-md border border-cyan-400/40 bg-cyan-400/10 px-5 py-3 text-sm font-semibold text-cyan-100"
          href="/dashboard"
        >
          Open Dashboard
        </Link>
      </section>
    </main>
  );
}
