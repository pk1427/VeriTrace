"use client";

import { useState } from "react";

const Btn =
  "rounded-md bg-black px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50";

export default function VerifyPage() {
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const run = async () => {
    if (!file) return;
    setLoading(true);
    const form = new FormData();
    form.set("file", file);
    try {
      const res = await fetch("/api/blockchain/verify", {
        method: "POST",
        body: form,
      });
      setResult(JSON.stringify(await res.json(), null, 2));
    } catch (err: unknown) {
      setResult(JSON.stringify({ ok: false, error: String(err) }));
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="mx-auto max-w-2xl px-6 py-12 font-sans">
      <h1 className="text-2xl font-bold">Verify on-chain</h1>
      <p className="mt-2 text-sm text-zinc-600">
        Upload a face photo to re-verify its evidence fingerprint against the
        deployed EvidenceRegistry contract (Amoy). Shows RECORDED vs UNRECORDED —
        any tampering invalidates the match.
      </p>

      <div className="mt-6 flex flex-col gap-3">
        <input
          type="file"
          accept="image/*"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <button onClick={run} disabled={loading || !file} className={Btn}>
          {loading ? "Verifying…" : "Verify"}
        </button>
      </div>

      {result && (
        <pre className="mt-4 overflow-x-auto text-xs">{result}</pre>
      )}
    </main>
  );
}
