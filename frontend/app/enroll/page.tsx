"use client";

import { useState } from "react";

const Input =
  "w-full rounded-md border border-zinc-300 px-3 py-2 text-sm outline-none ring-zinc-400 focus:ring-2";
const BtnPrimary =
  "rounded-md bg-black px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50";

export default function EnrollPage() {
  const [file, setFile] = useState<File | null>(null);
  const [subject, setSubject] = useState("Prasad Kapure");
  const [domains, setDomains] = useState("pk1427.github.io");
  const [consent, setConsent] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || !subject || !consent) return;
    setLoading(true);
    const form = new FormData();
    form.set("file", file);
    form.set("subject", subject);
    form.set("allowedDomains", domains);
    try {
      const res = await fetch("/api/enroll", { method: "POST", body: form });
      setResult(JSON.stringify(await res.json(), null, 2));
    } catch (err: unknown) {
      setResult(JSON.stringify({ ok: false, error: String(err) }));
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="mx-auto max-w-2xl px-6 py-12 font-sans">
      <h1 className="text-2xl font-bold">Enroll yourself</h1>
      <p className="mt-2 text-sm text-zinc-600">
        Upload a clear face photo of yourself. This is your explicit, owner-owned
        enrollment. Pick the domains that may be searched for you.
      </p>

      <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-4">
        <input
          type="file"
          accept="image/*"
          required
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <input
          className={Input}
          placeholder="Name / subject id"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          required
        />
        <input
          className={Input}
          placeholder="allowed-domains (comma-separated)"
          value={domains}
          onChange={(e) => setDomains(e.target.value)}
        />
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
            required
          />
          This is my face / I consent to enroll this person with their permission.
        </label>
        <button
          type="submit"
          disabled={loading || !consent || !file}
          className={BtnPrimary}
        >
          {loading ? "Enrolling…" : "Enroll"}
        </button>
      </form>

      {result && (
        <pre className="mt-4 overflow-x-auto text-xs">{result}</pre>
      )}
    </main>
  );
}
