const statusItems = [
  "Next.js App Router",
  "FastAPI backend",
  "PostgreSQL via Docker",
  "Prisma data layer"
];

export default function Home() {
  return (
    <main className="min-h-screen px-6 py-10 sm:px-10">
      <section className="mx-auto flex max-w-5xl flex-col gap-10">
        <div>
          <p className="text-sm font-semibold uppercase tracking-wide text-cyan-700">
            Base Platform
          </p>
          <h1 className="mt-4 max-w-3xl text-4xl font-semibold text-slate-950 sm:text-6xl">
            AI Network Digital Twin Lab
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-8 text-slate-700">
            A lean starting point for network simulation workflows, API services,
            and database-backed experiments.
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          {statusItems.map((item) => (
            <div
              className="rounded-md border border-slate-200 bg-white p-5 shadow-sm"
              key={item}
            >
              <p className="text-sm font-medium text-slate-500">Ready</p>
              <p className="mt-2 text-xl font-semibold text-slate-950">{item}</p>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
