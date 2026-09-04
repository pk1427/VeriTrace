"use client";

import { useMemo, useState } from "react";

type CommandResult = { ok: boolean; raw?: { stdout?: string; stderr?: string }; error?: string };
type Milestone = { label: string; detail: string; ok: boolean };
const primary = "rounded-xl bg-zinc-950 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40";
const secondary = "rounded-xl border border-zinc-300 bg-white px-4 py-2.5 text-sm font-semibold text-zinc-800 shadow-sm transition hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-40";

function outputOf(data: CommandResult | null) { return data?.raw?.stdout ?? data?.raw?.stderr ?? data?.error ?? ""; }
function parseFull(stdout: string): Milestone[] {
  const consent = stdout.match(/consent: GRANTED[^\n]*/i)?.[0] ?? "Consent gate did not open";
  const found = stdout.match(/\+\s+(\S+)\s+(https?:\/\/\S+).*similarity=([\d.-]+).*\[VERIFIED\]/)?.slice(1);
  const hash = stdout.match(/canonical\s+: sha256=([a-f0-9]{64})/i)?.[1];
  const recorded = /recorded\s+: True/i.test(stdout);
  const integrity = /INTEGRITY CONFIRMED/i.test(stdout);
  return [
    { label: "Consent gate", detail: consent, ok: /GRANTED/i.test(consent) },
    { label: "Live web match", detail: found ? `${found[0]} · similarity ${found[2]} · ${found[1]}` : "No verified public match returned", ok: Boolean(found) },
    { label: "Canonical evidence", detail: hash ? `SHA-256 ${hash}` : "Evidence hash not generated", ok: Boolean(hash) },
    { label: "Polygon Amoy", detail: recorded ? "Evidence record found on-chain" : "No on-chain record returned", ok: recorded },
    { label: "Independent verification", detail: integrity ? "MATCH — local hash equals on-chain hash" : "Verification did not confirm integrity", ok: integrity },
  ];
}

export default function RecordingDemo() {
  const [enrollmentPhoto, setEnrollmentPhoto] = useState<File | null>(null);
  const [scanPhoto, setScanPhoto] = useState<File | null>(null);
  const [subject, setSubject] = useState("prasad-demo");
  const [domains, setDomains] = useState("pk1427.github.io");
  const [consent, setConsent] = useState(false);
  const [resetMessage, setResetMessage] = useState("");
  const [enrollResult, setEnrollResult] = useState<CommandResult | null>(null);
  const [scanResult, setScanResult] = useState<CommandResult | null>(null);
  const [fullResult, setFullResult] = useState<CommandResult | null>(null);
  const [busy, setBusy] = useState<"reset" | "enroll" | "scan" | "full" | null>(null);
  const milestones = useMemo(() => parseFull(outputOf(fullResult)), [fullResult]);
  const enrolled = Boolean(enrollResult?.ok);

  async function resetDemo() {
    setBusy("reset"); setEnrollResult(null); setScanResult(null); setFullResult(null);
    try { const r = await fetch("/api/demo/reset", { method: "POST" }); const d = await r.json() as { message?: string; error?: string }; setResetMessage(d.message ?? d.error ?? "Unable to reset demo registry."); }
    finally { setBusy(null); }
  }
  async function postFile(url: string, file: File, extras?: Record<string, string>) {
    const form = new FormData(); form.set("file", file);
    Object.entries(extras ?? {}).forEach(([key, value]) => form.set(key, value));
    return await (await fetch(url, { method: "POST", body: form })).json() as CommandResult;
  }
  async function enroll() {
    if (!enrollmentPhoto || !consent) return;
    setBusy("enroll"); setEnrollResult(null); setScanResult(null); setFullResult(null);
    try { setEnrollResult(await postFile("/api/enroll", enrollmentPhoto, { subject, allowedDomains: domains })); } finally { setBusy(null); }
  }
  async function scan() {
    if (!scanPhoto) return;
    setBusy("scan"); setScanResult(null); setFullResult(null);
    try { setScanResult(await postFile("/api/scan", scanPhoto)); } finally { setBusy(null); }
  }
  async function runPipeline() {
    if (!scanPhoto) return;
    setBusy("full"); setFullResult(null);
    try { setFullResult(await postFile("/api/full", scanPhoto)); } finally { setBusy(null); }
  }

  return <main className="mx-auto min-h-screen max-w-5xl px-5 py-10 text-zinc-900 sm:px-8">
    <header className="rounded-3xl bg-zinc-950 px-7 py-9 text-white shadow-xl sm:px-10">
      <p className="font-mono text-xs uppercase tracking-[0.24em] text-emerald-300">HH Goa 2026 · Task 3</p>
      <h1 className="mt-3 text-4xl font-bold tracking-tight sm:text-5xl">VeriTrace</h1>
      <p className="mt-3 max-w-3xl text-zinc-300">Consent-gated face identification, real web discovery, and tamper-evident evidence verification on Polygon Amoy.</p>
    </header>
    <section className="mt-6 rounded-2xl border border-amber-200 bg-amber-50 p-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold">Start a clean recording session</h2><p className="mt-1 text-sm text-zinc-600">Resets only a disposable local demo registry; existing consent records are untouched.</p></div><button className={secondary} onClick={resetDemo} disabled={busy !== null}>{busy === "reset" ? "Resetting…" : "Reset demo registry"}</button></div>{resetMessage && <p className="mt-3 text-sm font-medium text-emerald-800">✓ {resetMessage}</p>}</section>
    <section className="mt-6 grid gap-5 lg:grid-cols-2">
      <Card number="1" title="Explicit owner enrollment" tone="emerald"><p className="text-sm text-zinc-600">The owner chooses their face photo and the exact public domain that may be searched.</p><label className="mt-4 block text-sm font-medium">Enrollment photo<input className="mt-1 block w-full text-sm" type="file" accept="image/*" onChange={(e) => setEnrollmentPhoto(e.target.files?.[0] ?? null)} /></label><label className="mt-3 block text-sm font-medium">Demo subject ID<input className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2" value={subject} onChange={(e) => setSubject(e.target.value)} /></label><label className="mt-3 block text-sm font-medium">Approved domain(s)<input className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 font-mono text-sm" value={domains} onChange={(e) => setDomains(e.target.value)} /></label><label className="mt-4 flex gap-2 text-sm"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />I confirm this owner explicitly consents to enrollment and scoped search.</label><button className={`${primary} mt-4`} onClick={enroll} disabled={busy !== null || !enrollmentPhoto || !consent}>{busy === "enroll" ? "Encoding face…" : "Enroll with consent"}</button><Result label="Enrollment" data={enrollResult} success="enrolled" /></Card>
      <Card number="2" title="Face scan input" tone="blue"><p className="text-sm text-zinc-600">Choose the image to prove. For the recorded happy path, select <code className="rounded bg-zinc-100 px-1">your_photo1.jpeg</code> again: it is the consented image with a live, publicly indexed matching page.</p><label className="mt-4 block text-sm font-medium">Proof image<input className="mt-1 block w-full text-sm" type="file" accept="image/*" onChange={(e) => setScanPhoto(e.target.files?.[0] ?? null)} /></label><button className={`${primary} mt-4`} onClick={scan} disabled={busy !== null || !scanPhoto || !enrolled}>{busy === "scan" ? "Detecting and encoding…" : "Detect and encode face"}</button><Result label="Face scan" data={scanResult} success="status       : ok" /></Card>
    </section>
    <section className="mt-6 rounded-2xl border border-violet-200 bg-gradient-to-br from-violet-50 to-white p-6 shadow-sm"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="font-mono text-xs font-bold uppercase tracking-[0.16em] text-violet-700">3 · Full verification pipeline</p><h2 className="mt-1 text-2xl font-bold">Consent → web match → Amoy proof</h2><p className="mt-1 text-sm text-zinc-600">Google Vision searches the consented domain, InsightFace verifies the returned face, then canonical evidence is anchored and re-checked on Polygon Amoy.</p></div><button className={primary} onClick={runPipeline} disabled={busy !== null || !scanPhoto || !enrolled || !scanResult?.ok}>{busy === "full" ? "Running live pipeline…" : "Run live verification"}</button></div>{fullResult && <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{milestones.map((item) => <div key={item.label} className={`rounded-xl border p-4 ${item.ok ? "border-emerald-200 bg-emerald-50" : "border-red-200 bg-red-50"}`}><p className="text-sm font-bold">{item.ok ? "✓" : "!"} {item.label}</p><p className="mt-2 break-all font-mono text-xs text-zinc-700">{item.detail}</p></div>)}</div>}<Result label="Full pipeline audit trail" data={fullResult} success="INTEGRITY CONFIRMED" open /></section>
    <footer className="mt-8 pb-4 text-center text-xs text-zinc-500">Private keys and biometric embeddings remain local; the browser receives only public verification metadata and command results.</footer>
  </main>;
}

function Card({ number, title, tone, children }: { number: string; title: string; tone: "emerald" | "blue"; children: React.ReactNode }) { const color = tone === "emerald" ? "bg-emerald-600" : "bg-blue-600"; return <section className="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-zinc-200"><div className="flex items-center gap-3"><span className={`${color} flex h-7 w-7 items-center justify-center rounded-full text-sm font-bold text-white`}>{number}</span><h2 className="text-xl font-bold">{title}</h2></div><div className="mt-3">{children}</div></section>; }
function Result({ label, data, success, open = false }: { label: string; data: CommandResult | null; success: string; open?: boolean }) { if (!data) return null; const output = outputOf(data); const passed = data.ok && output.toLowerCase().includes(success.toLowerCase()); return <details className="mt-4 rounded-xl border border-zinc-200 bg-zinc-50 p-3" open={open}><summary className={`cursor-pointer text-sm font-semibold ${passed ? "text-emerald-700" : "text-red-700"}`}>{passed ? "✓" : "!"} {label} {passed ? "completed" : "returned an error"}</summary><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-zinc-950 p-3 text-xs text-zinc-100">{output}</pre></details>; }
