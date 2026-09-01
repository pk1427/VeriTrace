"use client";

import { useState } from "react";

type StepStatus = "idle" | "open" | "verified" | "completed" | "error";
interface Step {
  label: string;
  body: string;
  status: StepStatus;
}

const Btn =
  "rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm font-medium text-black hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50";
const BtnPrimary =
  "rounded-md bg-black px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50";

export default function ScanPage() {
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingLabel, setLoadingLabel] = useState("");
  const [steps, setSteps] = useState<Step[]>([]);

  const addStep = (label: string, body: unknown, status: StepStatus) =>
    setSteps((s) => [...s, { label, body: JSON.stringify(body, null, 2), status }]);

  const call = async (label: string, url: string) => {
    if (!file) return;
    setLoading(true);
    setLoadingLabel(label);
    try {
      const form = new FormData();
      form.set("file", file);
      const res = await fetch(url, { method: "POST", body: form });
      const data = (await res.json()) as { ok?: boolean; raw?: { stdout?: string } };
      const out = data?.raw?.stdout ?? "";
      let status: StepStatus = "completed";
      if (!data?.ok) {
        status = "error";
      } else if (/gate\s*:\s*OPEN/i.test(out)) {
        status = "open";
      } else if (/\[VERIFIED\]/.test(out)) {
        status = "verified";
      } else if (/UNRECORDED|refusal:/i.test(out)) {
        status = "error";
      }
      addStep(label, data, status);
    } catch (err: unknown) {
      addStep(label, { ok: false, error: String(err) }, "error");
    } finally {
      setLoading(false);
      setLoadingLabel("");
    }
  };

  const onCheck = () => call("Consent gate", "/api/consent-check");
  const onSearch = () => call("Vision search", "/api/search");
  const onVerify = () => call("On-chain verify", "/api/blockchain/verify");

  const gateOpen = steps.some((s) => s.status === "open");
  const verified = steps.some((s) => s.status === "verified");

  const statusLabel = (s: Step): string =>
    s.status === "open"
      ? "gate OPEN"
      : s.status === "verified"
        ? "VERIFIED"
        : s.status === "error"
          ? "refused / error"
          : s.status === "completed"
            ? "completed"
            : "idle";

  return (
    <main className="mx-auto max-w-2xl px-6 py-12 font-sans">
      <h1 className="text-2xl font-bold">Scan a face</h1>
      <p className="mt-2 text-sm text-zinc-600">
        Upload any photo. VeriTrace enforces the consent gate first, then (if the
        subject is enrolled) runs a Vision reverse-image search over approved
        domains, then verifies the evidence on-chain.
      </p>

      <div className="mt-6 flex flex-col gap-3">
        <input
          type="file"
          accept="image/*"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <div className="flex flex-wrap gap-3">
          <button
            onClick={onCheck}
            disabled={loading || !file}
            className={BtnPrimary}
          >
            Check consent
          </button>
          <button
            onClick={onSearch}
            disabled={loading || !file || !gateOpen}
            className={Btn}
          >
            Search (Vision)
          </button>
          <button
            onClick={onVerify}
            disabled={loading || !file || !verified}
            className={Btn}
          >
            Verify on-chain
          </button>
        </div>
        {loading && <p className="text-sm text-zinc-500">Running {loadingLabel}…</p>}
      </div>

      <div className="mt-6 space-y-4">
        {steps.map((s, i) => (
          <div
            key={i}
            className="rounded-md border border-zinc-200 p-3"
          >
            <div className="flex items-center justify-between">
              <span className="font-medium">{s.label}</span>
              <span className="text-xs text-zinc-500">{statusLabel(s)}</span>
            </div>
            <pre className="mt-2 overflow-x-auto text-xs">{s.body}</pre>
          </div>
        ))}
      </div>
    </main>
  );
}
