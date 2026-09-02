import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { saveUpload, cleanupUpload, type SavedUpload } from "@/lib/upload";
import { runVeriTraceCmd } from "@/lib/runPythonCmd";
import type { VeriTraceResult } from "@/lib/types";

/**
 * POST /api/blockchain/anchor  — anchor this image's evidence fingerprint on
 * EvidenceRegistry.sol (consent-gated).
 *
 * The on-chain contract rejects re-anchoring an already-recorded fingerprint
 * (by design — the record is immutable). When that happens we transparently
 * fall through to `verify` and return the existing on-chain record so the
 * UI can show "this exact photo is already anchored (your earlier run did
 * it); the existing record is below" instead of a confusing error.
 *
 * Multipart upload: field `file`, optional `contract`, `rpc`, `key`,
 * `registry`, `outDir`.
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  let form: FormData;
  try {
    form = await req.formData();
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      { ok: false, error: "could not parse multipart body: " + msg },
      { status: 400 },
    );
  }
  const file = form.get("file");
  if (!file || typeof file === "string") {
    return NextResponse.json(
      { ok: false, error: "missing 'file' upload" },
      { status: 400 },
    );
  }

  let saved: SavedUpload | undefined;
  let anchorResult: VeriTraceResult;
  try {
    saved = await saveUpload(file as File);
    const args = buildAnchorArgs(form, saved.path);
    anchorResult = await runVeriTraceCmd("blockchain", args, { timeoutMs: 120_000 });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      { ok: false, error: "unexpected server error: " + msg },
      { status: 500 },
    );
  }

  // If the anchor reverted because the fingerprint is already on-chain,
  // automatically verify and return the existing record.
  if (!anchorResult.ok && isAlreadyAnchored(anchorResult)) {
    try {
      const verifyArgs = buildVerifyArgs(form, saved.path);
      const verifyResult = await runVeriTraceCmd("blockchain", verifyArgs, {
        timeoutMs: 120_000,
      });
      return NextResponse.json({
        ok: true,
        alreadyAnchored: true,
        message:
          "This exact photo is already anchored on this contract (re-anchoring is blocked by design). The existing on-chain record is below.",
        raw: verifyResult,
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      return NextResponse.json(
        { ok: false, error: "anchor reverted and follow-up verify failed: " + msg },
        { status: 500 },
      );
    } finally {
      if (saved) await cleanupUpload(saved);
    }
  }

  if (saved) await cleanupUpload(saved);
  return NextResponse.json({ ok: anchorResult.ok, raw: anchorResult });
}

function isAlreadyAnchored(res: VeriTraceResult): boolean {
  const blob = (res.stderr || "") + "\n" + (res.stdout || "");
  return /evidence already recorded|already anchored|reverted/i.test(blob);
}

function buildAnchorArgs(form: FormData, image: string): string[] {
  const args: string[] = ["anchor", image];
  const contract = String(form.get("contract") ?? "").trim();
  if (contract) args.push("--contract", contract);
  const rpc = String(form.get("rpc") ?? "").trim();
  if (rpc) args.push("--rpc", rpc);
  const key = String(form.get("key") ?? "").trim();
  if (key) args.push("--key", key);
  const registry = String(form.get("registry") ?? "").trim();
  if (registry) args.push("--registry", registry);
  const outDir = String(form.get("outDir") ?? "").trim();
  if (outDir) args.push("--out-dir", outDir);
  return args;
}

function buildVerifyArgs(form: FormData, image: string): string[] {
  const args: string[] = ["verify", image];
  const contract = String(form.get("contract") ?? "").trim();
  if (contract) args.push("--contract", contract);
  const rpc = String(form.get("rpc") ?? "").trim();
  if (rpc) args.push("--rpc", rpc);
  const registry = String(form.get("registry") ?? "").trim();
  if (registry) args.push("--registry", registry);
  return args;
}
