export default function Home() {
  return (
    <main className="flex min-h-screen items-center justify-center">
      <div className="text-center">
        <h1 className="text-4xl font-semibold tracking-tight">Vinea ERP</h1>
        <p className="mt-3 text-stone-500">Phase 0 scaffold — kernel construction begins in P1/P2.</p>
        <p className="mt-1 text-sm text-[var(--vinea-leaf)]">API: {process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/health</p>
      </div>
    </main>
  );
}
