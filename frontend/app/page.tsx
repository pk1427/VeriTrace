"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  pillClasses,
  STATUS_COLOR,
  STATUS_LABEL,
} from "@/lib/colors";
import {
  parseSearchOutput,
  parseSearchSummary,
  type Candidate,
  type SearchSummary,
} from "@/lib/parseSearch";
import { tamperFile } from "@/lib/tamper";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface RegistrySubject {
  subject_id: string;
  enrolled_at: string;
  enrollment_photo: string;
  allowed_domains: string[] | null;
}

interface Registry {
  version: string;
  match_threshold: number;
  subjects: RegistrySubject[];
}

interface StepResult {
  ok: boolean;
  stdout: string;
  stderr: string;
  code: number | null;
  durationMs?: number;
  raw?: { ok: boolean; stdout: string; stderr: string; code: number | null; durationMs?: number };
  alreadyAnchored?: boolean;
  message?: string;
}

/* ------------------------------------------------------------------ */
/*  Page                                                               */
/* ------------------------------------------------------------------ */

export default function RecordingPage() {
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [registryError, setRegistryError] = useState<string | null>(null);

  const [subjectId, setSubjectId] = useState<string>("prasad");
  const [consentResult, setConsentResult] = useState<{
    granted: boolean;
    bestSubject: string;
    bestScore: number;
    threshold: number;
    raw: StepResult;
  } | null>(null);

  const [photo, setPhoto] = useState<File | null>(null);
  const [searchRunning, setSearchRunning] = useState(false);
  const [searchResult, setSearchResult] = useState<{
    summary: SearchSummary;
    candidates: Candidate[];
    raw: StepResult;
  } | null>(null);

  const [anchorRunning, setAnchorRunning] = useState(false);
  const [anchorResult, setAnchorResult] = useState<StepResult | null>(null);

  const [verifyFile, setVerifyFile] = useState<File | null>(null);
  const [verifyRunning, setVerifyRunning] = useState(false);
  const [verifyResult, setVerifyResult] = useState<{
    ok: boolean;
    stdout: string;
  } | null>(null);

  const [tamperFileState, setTamperFileState] = useState<File | null>(null);
  const [tamperRunning, setTamperRunning] = useState(false);
  const [tamperResult, setTamperResult] = useState<{
    ok: boolean;
    stdout: string;
  } | null>(null);

  /* Load the registry once on mount. */
  useEffect(() => {
    fetch("/api/registry")
      .then((r) => r.json())
      .then((d) => {
        if (d?.ok && d.registry) setRegistry(d.registry as Registry);
        else setRegistryError(d?.error || "unknown registry error");
      })
      .catch((e: unknown) =>
        setRegistryError(e instanceof Error ? e.message : String(e)),
      );
  }, []);

  const selectedSubject = useMemo(
    () => registry?.subjects.find((s) => s.subject_id === subjectId) ?? null,
    [registry, subjectId],
  );
  void selectedSubject;

  const selectPhoto = (nextPhoto: File | null) => {
    setPhoto(nextPhoto);
    // A result always belongs to the exact bytes that were uploaded. Do not
    // let an earlier consent, search, or chain result appear valid for a new
    // image.
    setConsentResult(null);
    setSearchResult(null);
    setAnchorResult(null);
    setVerifyFile(null);
    setVerifyResult(null);
    setTamperFileState(null);
    setTamperResult(null);
  };

  const handleAnchorPreCheck = useCallback((result: StepResult) => {
    setAnchorResult(result);
  }, []);

  /* -------------------------- actions -------------------------- */

  const runConsent = async () => {
    if (!subjectId) return;
    const form = new FormData();
    form.set("subject", subjectId);
    form.set("file", new File([new Uint8Array(0)], "consent-only.txt"));
    /* The CLI's `check` requires an image with a detectable face. If the
       registry has the subject's enrollment photo, we can call check on
       that file directly (it's the cheapest, most reliable path). */
    const subj = registry?.subjects.find((s) => s.subject_id === subjectId);
    if (!subj) return;
    /* Use a server-side trick: POST to /api/consent-check with the
       enrollment_photo filename; the server can't read it, so instead we
       call the Python `check` command directly via a small bypass. For
       simplicity in the recording, the consent step just reads the
       registry's stored score from a separate /api/registry route
       fallback — but here we keep it simple: re-use /api/scan output
       from the user's chosen photo. */
    void form;
    /* Actually: use the user's `photo` (the same file they'll search with)
       so the consent check is a real end-to-end run on the real file. */
    if (!photo) {
      alert("Choose a photo first (the same one you will search with).");
      return;
    }
    const real = new FormData();
    real.set("file", photo);
    const r = await fetch("/api/consent-check", { method: "POST", body: real });
    const data = (await r.json()) as { ok: boolean; raw: StepResult };
    const stdout = data?.raw?.stdout || "";
    const mScore = stdout.match(/score=(-?\d+\.\d+)/);
    const mThresh = stdout.match(/>=\s*(-?\d+\.\d+)/) || stdout.match(/>=\s*(\d+\.\d+)/);
    const mGranted = /GRANTED/.test(stdout);
    const score = mScore ? Number(mScore[1]) : NaN;
    setConsentResult({
      granted: mGranted,
      bestSubject: subj.subject_id,
      bestScore: score,
      threshold: mThresh ? Number(mThresh[1]) : 0.6,
      raw: data.raw,
    });
  };

  const runSearch = async () => {
    if (!photo) return;
    setSearchRunning(true);
    setSearchResult(null);
    try {
      const form = new FormData();
      form.set("file", photo);
      const r = await fetch("/api/search", { method: "POST", body: form });
      const data = (await r.json()) as { ok: boolean; raw: StepResult };
      const stdout = data?.raw?.stdout || "";
      setSearchResult({
        summary: parseSearchSummary(stdout),
        candidates: parseSearchOutput(stdout),
        raw: data.raw,
      });
    } finally {
      setSearchRunning(false);
    }
  };

  const runAnchor = async () => {
    if (!photo) return;
    setAnchorRunning(true);
    try {
      const form = new FormData();
      form.set("file", photo);
      const r = await fetch("/api/blockchain/anchor", {
        method: "POST",
        body: form,
      });
      const data = (await r.json()) as StepResult & {
        alreadyAnchored?: boolean;
        message?: string;
      };
      setAnchorResult(data);
    } finally {
      setAnchorRunning(false);
    }
  };

  const runVerify = async (fileToVerify: File) => {
    if (!fileToVerify) return;
    setVerifyRunning(true);
    try {
      const form = new FormData();
      form.set("file", fileToVerify);
      const r = await fetch("/api/blockchain/verify", {
        method: "POST",
        body: form,
      });
      const data = (await r.json()) as { ok: boolean; raw: StepResult };
      setVerifyResult({ ok: data.ok, stdout: data?.raw?.stdout || "" });
    } finally {
      setVerifyRunning(false);
    }
  };

  const runTamperDemo = async () => {
    if (!photo) return;
    setTamperRunning(true);
    setTamperResult(null);
    try {
      const tampered = await tamperFile(photo);
      setTamperFileState(tampered);
      const form = new FormData();
      form.set("file", tampered);
      const r = await fetch("/api/blockchain/verify", {
        method: "POST",
        body: form,
      });
      const data = (await r.json()) as { ok: boolean; raw: StepResult };
      setTamperResult({ ok: data.ok, stdout: data?.raw?.stdout || "" });
    } finally {
      setTamperRunning(false);
    }
  };

  /* -------------------------- render -------------------------- */

  return (
    <main className="mx-auto max-w-6xl px-6 py-10 font-sans">
      {/* Hero / elevator pitch */}
      <section className="relative overflow-hidden rounded-3xl bg-white shadow-sm">
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.04]"
          aria-hidden
          style={{
            backgroundImage:
              "radial-gradient(circle at 1px 1px, #18181b 1px, transparent 0)",
            backgroundSize: "18px 18px",
          }}
        />
        <div className="relative px-8 py-10 sm:px-12 sm:py-14">
          <div className="flex items-center gap-3">
            <span
              className="flex h-10 w-10 items-center justify-center rounded-xl bg-zinc-900 font-mono text-base font-bold text-white"
              aria-hidden
            >
              V
            </span>
            <div>
              <div className="font-mono text-xs uppercase tracking-[0.18em] text-zinc-500">
                VeriTrace
              </div>
              <div className="text-xs text-zinc-500">
                HH Goa 2026 · Task 3 · face + blockchain
              </div>
            </div>
          </div>

          <h1 className="mt-6 text-4xl font-bold leading-tight tracking-tight text-zinc-900 sm:text-5xl">
            Prove a face was on the web —
            <br className="hidden sm:block" />
            <span className="text-zinc-500">
              {" "}
              then anchor the proof to a blockchain.
            </span>
          </h1>

          <p className="mt-4 max-w-3xl text-base text-zinc-700">
            VeriTrace only searches the web for faces of people who have
            <strong> explicitly enrolled themselves</strong>. The match is
            fingerprinted (SHA-256) and written to{" "}
            <a
              href="https://amoy.polygonscan.com/address/0x6650630946313835E5a1175ddE8F9969674fbE28"
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono text-purple-700 underline decoration-purple-300 underline-offset-2 hover:decoration-purple-600"
            >
              EvidenceRegistry
            </a>{" "}
            on Polygon Amoy. Anyone can re-verify the same photo and see if it
            was tampered with.
          </p>

          <ol className="mt-8 grid gap-3 sm:grid-cols-3">
            <li className="rounded-2xl bg-gradient-to-br from-green-50 to-white p-4">
              <div className="flex items-center gap-2">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-green-600 font-mono text-xs font-bold text-white">
                  1
                </span>
                <span className="text-xs font-semibold uppercase tracking-wide text-green-800">
                  Consent
                </span>
              </div>
              <p className="mt-2 text-sm text-zinc-700">
                Owner enrolls their face and pre-approves which websites may be
                searched for them.
              </p>
            </li>
            <li className="rounded-2xl bg-gradient-to-br from-blue-50 to-white p-4">
              <div className="flex items-center gap-2">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-blue-600 font-mono text-xs font-bold text-white">
                  2
                </span>
                <span className="text-xs font-semibold uppercase tracking-wide text-blue-800">
                  Search
                </span>
              </div>
              <p className="mt-2 text-sm text-zinc-700">
                Upload a photo → Google Vision finds public matches → filter to
                approved domains → verify each face.
              </p>
            </li>
            <li className="rounded-2xl bg-gradient-to-br from-purple-50 to-white p-4">
              <div className="flex items-center gap-2">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-purple-700 font-mono text-xs font-bold text-white">
                  3
                </span>
                <span className="text-xs font-semibold uppercase tracking-wide text-purple-800">
                  Anchor
                </span>
              </div>
              <p className="mt-2 text-sm text-zinc-700">
                SHA-256 fingerprint is written to Polygon Amoy. Re-verify any
                photo — one byte off and the record is{" "}
                <span className="font-mono">UNRECORDED</span>.
              </p>
            </li>
          </ol>
        </div>
      </section>

      {/* Enrolled subjects panel */}
      <section className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
          Enrolled subjects
        </h2>
        <p className="mt-1 text-sm text-zinc-600">
          Each person pre-approved the exact sites that may be searched.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {registryError && (
            <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">
              Could not load registry: {registryError}
            </div>
          )}
          {registry?.subjects.map((s) => {
            const initials = s.subject_id.slice(0, 2).toUpperCase();
            return (
              <div
                key={s.subject_id}
                className="rounded-xl bg-white p-4 shadow-sm"
              >
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-zinc-100 font-mono text-sm font-bold text-zinc-700">
                    {initials}
                  </div>
                  <div>
                    <div className="font-semibold">{s.subject_id}</div>
                    <div className="text-xs text-zinc-500">
                      enrolled {formatTs(s.enrolled_at)}
                    </div>
                  </div>
                </div>
                <div className="mt-3">
                  <div className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                    Approved domains
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                    {(s.allowed_domains ?? []).map((d) => (
                      <a
                        key={d}
                        href={`https://${d}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-mono text-xs text-blue-700 underline decoration-blue-300 underline-offset-2 hover:text-blue-800 hover:decoration-blue-600"
                      >
                        {d}
                      </a>
                    ))}
                    {(!s.allowed_domains || s.allowed_domains.length === 0) && (
                      <span className="text-xs text-zinc-500">
                        (default list)
                      </span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
        <p className="mt-3 text-xs text-zinc-500">
          Enrollment photo is stored locally only (gitignored biometric data) and is
          not displayed here.
        </p>
      </section>

      {/* STEP 1 — Consent gate */}
      <Step number={1} title="Choose a photo & check consent" color="green">
        <p className="text-sm text-zinc-600">
          Compares the face to enrolled embeddings. Search is blocked unless the
          gate is OPEN.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-dashed border-zinc-300 bg-white/70 p-3">
          <input
            type="file"
            accept="image/*"
            onChange={(e) => selectPhoto(e.target.files?.[0] ?? null)}
            className="max-w-full text-sm"
          />
          {photo ? (
            <>
              <PreviewThumb file={photo} />
              <span className="text-xs text-zinc-600">{photo.name}</span>
            </>
          ) : (
            <span className="text-xs text-zinc-500">Select the photo you want to prove.</span>
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <label className="text-sm">
            Subject:{" "}
            <select
              className="rounded-md border border-zinc-300 bg-white px-2 py-1 text-sm"
              value={subjectId}
              onChange={(e) => setSubjectId(e.target.value)}
            >
              {registry?.subjects.map((s) => (
                <option key={s.subject_id} value={s.subject_id}>
                  {s.subject_id}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={runConsent}
            disabled={!photo}
            className="rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Check consent
          </button>
          {photo && <span className="text-xs text-zinc-500">using {photo.name}</span>}
        </div>
        {consentResult && (
          <div className="mt-3">
            {consentResult.granted ? (
              <span
                className={pillClasses("green")}
                data-testid="consent-pill"
              >
                <Dot /> gate OPEN — {consentResult.bestSubject} · score{" "}
                {consentResult.bestScore.toFixed(4)} ≥{" "}
                {consentResult.threshold.toFixed(2)}
              </span>
            ) : (
              <span className={pillClasses("red")}>
                <Dot /> gate CLOSED — best {consentResult.bestSubject} · score{" "}
                {consentResult.bestScore.toFixed(4)} &lt;{" "}
                {consentResult.threshold.toFixed(2)}
              </span>
            )}
            {consentResult && !consentResult.granted && (
              <p className="mt-2 text-xs text-zinc-600">
                Search refused — subject is not enrolled. This is the hard gate,
                by design.
              </p>
            )}
          </div>
        )}
      </Step>

      {/* STEP 2 — Search */}
      <Step number={2} title="Search approved websites" color="blue">
        <p className="text-sm text-zinc-600">
          Google Vision finds public matches. We keep only URLs on the
          subject&apos;s approved domains, then verify each face.
        </p>
        <div className="mt-3">
          <button
            onClick={runSearch}
            disabled={!photo || !consentResult?.granted || searchRunning}
            className="rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {searchRunning ? "Searching…" : "Search the web for this face"}
          </button>
        </div>
        {searchResult && (
          <div className="mt-4">
            <div className="text-xs text-zinc-600">
              {searchResult.summary.rawCount ?? "?"} raw results ·{" "}
              {searchResult.summary.candidateCount ?? "?"} after domain filter
              · {searchResult.summary.verifiedCount ?? "?"} VERIFIED ·{" "}
              {searchResult.summary.rejectedCount ?? "?"} rejected
            </div>
            <CandidateList candidates={searchResult.candidates} />
          </div>
        )}
      </Step>

      {/* STEP 3 — Anchor */}
      {searchResult && (searchResult.summary.verifiedCount ?? 0) > 0 && (
        <Step number={3} title="Anchor to blockchain" color="purple">
          <p className="text-sm text-zinc-600">
            The photo&apos;s SHA-256 is written to the{" "}
            <code className="rounded bg-zinc-100 px-1 py-0.5 text-xs">
              EvidenceRegistry
            </code>{" "}
            contract on Polygon Amoy. Once written, it cannot be removed.
          </p>

          {/* Pre-click: show the existing on-chain record (if any) so the
              user can see this photo is already anchored before pressing the
              button. The click is then a confirmation, not a fresh action. */}
          {!anchorResult && (
            <AnchorPreCheck
              photo={photo}
              onResult={handleAnchorPreCheck}
            />
          )}

          <div className="mt-3">
            <button
              onClick={runAnchor}
              disabled={anchorRunning}
              className="rounded-md bg-purple-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-purple-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {anchorRunning
                ? "Anchoring…"
                : anchorResult?.alreadyAnchored
                  ? "Confirm on-chain record"
                  : "Anchor this evidence on Polygon Amoy"}
            </button>
          </div>
          {anchorResult && (
            <EvidenceCard
              stdout={anchorResult.raw?.stdout ?? anchorResult.stdout ?? ""}
              alreadyAnchored={anchorResult.alreadyAnchored}
              message={anchorResult.message}
            />
          )}
        </Step>
      )}

      {/* Re-verify + tamper demo */}
      <section className="mt-10">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
          Re-verify on-chain
        </h2>
        <p className="mt-1 text-sm text-zinc-600">
          Re-check the on-chain record for any photo. The tamper demo proves
          that one byte change invalidates the fingerprint.
        </p>

        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <div className="rounded-xl bg-white p-4 shadow-sm">
            <h3 className="font-semibold">Verify a photo is anchored</h3>
            <p className="mt-1 text-xs text-zinc-500">
              Pick the original photo, then click Re-verify.
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <input
                type="file"
                accept="image/*"
                onChange={(e) =>
                  setVerifyFile(e.target.files?.[0] ?? null)
                }
                className="text-sm"
              />
            </div>
            <div className="mt-2">
              <button
                onClick={() => runVerify(verifyFile!)}
                disabled={!verifyFile || verifyRunning}
                className="rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {verifyRunning ? "Verifying…" : "Re-verify on-chain"}
              </button>
            </div>
            {verifyResult && <ChainBadge stdout={verifyResult.stdout} />}
          </div>

          <div className="rounded-xl bg-white p-4 shadow-sm">
            <h3 className="font-semibold">Tamper demo</h3>
            <p className="mt-1 text-xs text-zinc-500">
              One byte change → different SHA-256 → UNRECORDED.
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                onClick={() => photo && runVerify(photo)}
                disabled={!photo || verifyRunning}
                className="rounded-md border border-green-600/50 bg-green-50 px-3 py-1.5 text-sm font-medium text-green-800 hover:bg-green-100 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Use the original photo
              </button>
              <button
                onClick={runTamperDemo}
                disabled={!photo || tamperRunning}
                className="rounded-md border border-zinc-300 bg-zinc-50 px-3 py-1.5 text-sm font-medium text-zinc-800 hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {tamperRunning
                  ? "Tampering…"
                  : "Use a tampered photo (flip 1 byte)"}
              </button>
            </div>
            {tamperResult && (
              <div className="mt-3">
                <ChainBadge stdout={tamperResult.stdout} />
                {tamperFileState && (
                  <p className="mt-1 text-[11px] text-zinc-500">
                    Tampered file: {tamperFileState.name} (
                    {tamperFileState.size} bytes)
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      </section>

      <footer className="mt-12 text-xs text-zinc-500">
        VeriTrace — HH Goa 2026 Task 3. Pipeline: face in → owner-scoped web
        search → SHA-256 evidence on Polygon Amoy → independent re-verification.
      </footer>
    </main>
  );
}

/* ------------------------------------------------------------------ */
/*  Small inline components                                            */
/* ------------------------------------------------------------------ */

function Dot() {
  return (
    <span
      className="inline-block h-1.5 w-1.5 rounded-full"
      style={{ background: "currentColor" }}
    />
  );
}

function Step({
  number,
  title,
  color,
  children,
}: {
  number: number;
  title: string;
  color: "green" | "blue" | "purple";
  children: React.ReactNode;
}) {
  const tint =
    color === "green"
      ? "bg-green-50/60"
      : color === "blue"
        ? "bg-blue-50/60"
        : "bg-purple-50/60";
  const accent =
    color === "green"
      ? "bg-green-600"
      : color === "blue"
        ? "bg-blue-600"
        : "bg-purple-700";
  const badge =
    color === "green"
      ? "bg-green-600 text-white"
      : color === "blue"
        ? "bg-blue-600 text-white"
        : "bg-purple-700 text-white";
  return (
    <section
      className={`mt-10 overflow-hidden rounded-2xl bg-white shadow-sm ${tint}`}
    >
      <div className={`h-1 w-full ${accent}`} aria-hidden />
      <div className="p-6">
        <div className="flex items-center gap-3">
          <span
            className={`flex h-7 w-7 items-center justify-center rounded-full font-mono text-sm font-bold ${badge}`}
          >
            {number}
          </span>
          <h2 className="text-lg font-semibold">{title}</h2>
        </div>
        <div className="mt-3">{children}</div>
      </div>
    </section>
  );
}

function PreviewThumb({ file }: { file: File }) {
  const url = useMemo(() => URL.createObjectURL(file), [file]);
  useEffect(() => () => URL.revokeObjectURL(url), [url]);
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={url}
      alt="preview"
      className="h-12 w-12 rounded-md border border-zinc-200 object-cover"
    />
  );
}

function CandidateList({ candidates }: { candidates: Candidate[] }) {
  if (candidates.length === 0) {
    return (
      <p className="mt-3 text-sm text-zinc-500">
        No candidates were kept after the domain filter.
      </p>
    );
  }
  return (
    <div className="mt-3 overflow-x-auto rounded-lg shadow-sm">
      <table className="w-full text-sm">
        <thead className="bg-zinc-50 text-xs uppercase text-zinc-500">
          <tr>
            <th className="px-3 py-2 text-left">#</th>
            <th className="px-3 py-2 text-left">Host</th>
            <th className="px-3 py-2 text-left">URL</th>
            <th className="px-3 py-2 text-right">Similarity</th>
            <th className="px-3 py-2 text-left">Status</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c, i) => {
            const color = STATUS_COLOR[c.status];
            return (
              <tr
                key={`${c.host}-${i}`}
                className="border-t border-zinc-100 align-top"
              >
                <td className="px-3 py-2 font-mono text-xs text-zinc-500">
                  {i + 1}
                </td>
                <td className="px-3 py-2 font-mono text-xs">{c.host}</td>
                <td className="px-3 py-2 font-mono text-xs break-all text-zinc-600">
                  <a
                    href={c.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-700 underline decoration-blue-300 underline-offset-2 hover:text-blue-800 hover:decoration-blue-600"
                    title={c.url}
                  >
                    {truncate(c.url, 70)}
                  </a>
                  {c.reason && (
                    <div className="mt-1 text-[11px] text-zinc-500">
                      reason: {c.reason}
                    </div>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {c.similarity !== undefined ? c.similarity.toFixed(4) : "—"}
                </td>
                <td className="px-3 py-2">
                  <span className={pillClasses(color)}>
                    {STATUS_LABEL[c.status]}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function EvidenceCard({
  stdout,
  alreadyAnchored,
  message,
}: {
  stdout: string | null | undefined;
  alreadyAnchored?: boolean;
  message?: string;
}) {
  const fp = extract(stdout, /fingerprint\s*:\s*([0-9a-f]{64})/i);
  const tx = extract(stdout, /tx\s*:\s*(0x[0-9a-f]{64})/i);
  const submitter = extract(stdout, /submitter=([0-9a-fxA-F]{40,42})/);
  const createdAt = extract(stdout, /createdAt=(\d+)/);
  const blockMatch = extract(stdout, /block(?:Number|_number)?\s*[:=]?\s*(\d+)/i);
  const recorded = /RECORDED|on-chain\s*:.*RECORDED/i.test(stdout || "");
  // Default Amoy deployment per the README; can be overridden via
  // NEXT_PUBLIC_CONTRACT_ADDRESS for future re-deployments.
  const contractAddress =
    process.env.NEXT_PUBLIC_CONTRACT_ADDRESS ||
    "0x6650630946313835E5a1175ddE8F9969674fbE28";
  const contractLink = `https://amoy.polygonscan.com/address/${contractAddress}`;
  const contractTxsLink = `https://amoy.polygonscan.com/address/${contractAddress}#transactions`;
  const fingerprintLink = recorded ? contractLink : undefined;

  return (
    <div className="mt-3 overflow-hidden rounded-2xl bg-white shadow-sm">
      <div className="h-1 w-full bg-purple-700" aria-hidden />
      <div className="p-4">
      {alreadyAnchored && (
        <div className="mb-3 rounded-md border border-yellow-600/40 bg-yellow-50 p-3 text-sm text-yellow-800">
          <div className="font-semibold">
            ✓ This photo is already anchored on-chain
          </div>
          <div className="mt-1 text-xs">
            Re-anchoring the same fingerprint is blocked by the contract (the
            record is immutable). The existing on-chain record is shown below.
          </div>
          {submitter && createdAt && (
            <div className="mt-2 text-xs">
              <span className="font-semibold">Original anchor:</span>{" "}
              submitter <span className="font-mono">{submitter}</span>, createdAt{" "}
              <span className="font-mono">{createdAt}</span>.
            </div>
          )}
          <a
            href={contractTxsLink}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-2 inline-block text-xs font-medium text-purple-700 underline hover:text-purple-800"
          >
            Look up the original anchor transaction on amoy.polygonscan.com ↗
          </a>
        </div>
      )}
      {!alreadyAnchored && message && (
        <div className="mb-3 rounded-md border border-yellow-600/40 bg-yellow-50 p-3 text-sm text-yellow-800">
          {message}
        </div>
      )}
      <div className="grid gap-2 text-sm">
        <Row
          label="Fingerprint (SHA-256)"
          value={fp || "—"}
          mono
          copy
          link={fp ? fingerprintLink : undefined}
        />
        {tx ? (
          <Row
            label="Tx hash"
            value={tx}
            mono
            copy
            link={`https://amoy.polygonscan.com/tx/${tx}`}
          />
        ) : recorded ? (
          <Row
            label="Tx hash"
            value="(already anchored earlier — see contract tx list)"
            link={contractTxsLink}
          />
        ) : (
          <Row label="Tx hash" value="—" />
        )}
        {blockMatch && <Row label="Block" value={blockMatch} mono />}
        {submitter && <Row label="Submitter" value={submitter} mono />}
        {recorded && (
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className={pillClasses("purple")}>
              <Dot /> on-chain RECORDED
            </span>
            <a
              href={contractTxsLink}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-purple-700 underline hover:text-purple-800"
            >
              View all anchor transactions on amoy.polygonscan.com ↗
            </a>
          </div>
        )}
        {createdAt && !blockMatch && (
          <div className="text-xs text-zinc-600">
            <span className="font-semibold uppercase tracking-wide text-zinc-500">
              Recorded at
            </span>{" "}
            <span className="font-mono">{createdAt}</span>
          </div>
        )}
      </div>
      </div>
    </div>
  );
}

/**
 * Pre-click check for Step 3: silently verify the photo against the on-chain
 * registry. If it's already anchored, surface a clear info notification so
 * the user knows before they click "Anchor". The click then becomes a
 * confirmation rather than a surprising "already exists" result.
 */
function AnchorPreCheck({
  photo,
  onResult,
}: {
  photo: File | null;
  onResult: (r: StepResult) => void;
}) {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "recorded"; submitter: string | null; createdAt: string | null; fingerprint: string | null }
    | { kind: "unrecorded" }
    | { kind: "error"; message: string }
  >({ kind: "loading" });

  useEffect(() => {
    if (!photo) return;
    let cancelled = false;
    (async () => {
      try {
        const form = new FormData();
        form.set("file", photo);
        const r = await fetch("/api/blockchain/verify", {
          method: "POST",
          body: form,
        });
        const data = (await r.json()) as { ok: boolean; raw: StepResult };
        if (cancelled) return;
        const out = data?.raw?.stdout || "";
        const recorded = /RECORDED|on-chain\s*:.*RECORDED/i.test(out);
        if (recorded) {
          const submitter = extract(out, /submitter=([0-9a-fxA-F]{40,42})/);
          const createdAt = extract(out, /createdAt=(\d+)/);
          const fingerprint = extract(out, /fingerprint\s*:\s*([0-9a-f]{64})/i);
          setState({ kind: "recorded", submitter, createdAt, fingerprint });
          onResult({
            ok: true,
            stdout: out,
            stderr: "",
            code: 0,
            alreadyAnchored: true,
            message:
              "This exact photo is already anchored on the contract. The existing on-chain record is shown below.",
          });
        } else {
          setState({ kind: "unrecorded" });
        }
      } catch (e: unknown) {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : String(e);
        setState({ kind: "error", message: msg });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [photo, onResult]);

  const contractAddress =
    process.env.NEXT_PUBLIC_CONTRACT_ADDRESS ||
    "0x6650630946313835E5a1175ddE8F9969674fbE28";
  const contractTxsLink = `https://amoy.polygonscan.com/address/${contractAddress}#transactions`;

  if (state.kind === "loading" || state.kind === "error") {
    return null;
  }
  if (state.kind === "unrecorded") {
    return (
      <div className="mt-3 rounded-md border border-blue-600/30 bg-blue-50 p-3 text-sm text-blue-900">
        <div className="font-semibold">Not yet anchored on-chain.</div>
        <div className="mt-1 text-xs">
          Clicking below will sign a transaction to write the SHA-256
          fingerprint of this photo to the EvidenceRegistry contract on Polygon
          Amoy.
        </div>
      </div>
    );
  }
  // state.kind === "recorded"
  return (
    <div className="mt-3 rounded-md border border-green-600/30 bg-green-50 p-3 text-sm text-green-900">
      <div className="font-semibold">✓ This photo is already anchored on-chain.</div>
      {state.fingerprint && (
        <div className="mt-1 break-all text-xs">
          <span className="font-semibold">Fingerprint:</span>{" "}
          <span className="font-mono">{state.fingerprint}</span>
        </div>
      )}
      {state.submitter && state.createdAt && (
        <div className="mt-1 text-xs">
          <span className="font-semibold">Original anchor:</span> submitter{" "}
          <span className="font-mono">{state.submitter}</span>, createdAt{" "}
          <span className="font-mono">{state.createdAt}</span>
        </div>
      )}
      <a
        href={contractTxsLink}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-2 inline-block text-xs font-medium text-purple-700 underline hover:text-purple-800"
      >
        Look up the original anchor transaction on amoy.polygonscan.com ↗
      </a>
    </div>
  );
}

function ChainBadge({ stdout }: { stdout: string }) {
  const recorded = /RECORDED|on-chain\s*:.*RECORDED/i.test(stdout);
  const submitter = extract(stdout, /submitter=([0-9a-fxA-F]{40,42})/);
  const fp = extract(stdout, /fingerprint\s*:\s*([0-9a-f]{64})/i);
  const ts = extract(stdout, /createdAt=(\d+)/);
  if (recorded) {
    return (
      <div className="mt-3 rounded-md border border-green-600/40 bg-green-50 p-3 text-sm">
        <span className={pillClasses("green")}>
          <Dot /> on-chain RECORDED
        </span>
        <div className="mt-2 text-xs text-zinc-700">
          {submitter && (
            <div>
              <span className="font-semibold">submitter:</span>{" "}
              <span className="font-mono">{submitter}</span>
            </div>
          )}
          {ts && (
            <div>
              <span className="font-semibold">createdAt:</span>{" "}
              <span className="font-mono">{ts}</span>
            </div>
          )}
          {fp && (
            <div className="break-all">
              <span className="font-semibold">fingerprint:</span>{" "}
              <span className="font-mono">{fp}</span>
            </div>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="mt-3 rounded-md border border-zinc-300 bg-zinc-50 p-3 text-sm">
      <span className={pillClasses("grey")}>
        <Dot /> UNRECORDED
      </span>
      {fp && (
        <p className="mt-2 break-all text-xs text-zinc-600">
          SHA-256 checked: <span className="font-mono">{fp}</span>
        </p>
      )}
      <p className="mt-1 text-xs text-zinc-500">
        The contract has no record of this fingerprint. Any byte change to the
        original photo produces this outcome.
      </p>
    </div>
  );
}

function Row({
  label,
  value,
  mono,
  copy,
  link,
}: {
  label: string;
  value: string;
  mono?: boolean;
  copy?: boolean;
  link?: string;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
        {label}
      </span>
      <span
        className={`break-all text-sm ${mono ? "font-mono" : ""} text-zinc-800`}
      >
        {link ? (
          <a
            href={link}
            target="_blank"
            rel="noopener noreferrer"
            className="text-purple-700 underline hover:text-purple-800"
          >
            {value} ↗
          </a>
        ) : (
          value
        )}
      </span>
      {copy && value !== "—" && (
        <button
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(value);
              setCopied(true);
              setTimeout(() => setCopied(false), 1200);
            } catch {
              /* ignore */
            }
          }}
          className="rounded border border-zinc-300 bg-white px-1.5 py-0.5 text-[10px] font-medium text-zinc-700 hover:bg-zinc-100"
        >
          {copied ? "copied" : "copy"}
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function extract(s: string | null | undefined, re: RegExp): string | null {
  if (typeof s !== "string" || !s) return null;
  const m = s.match(re);
  return m && m[1] ? m[1] : null;
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}

function formatTs(iso: string): string {
  if (!iso) return "—";
  try {
    return iso.replace("T", " ").replace(/\.\d+/, "").replace("+00:00", " UTC");
  } catch {
    return iso;
  }
}
