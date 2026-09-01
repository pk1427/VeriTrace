import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { handleFileCmd } from "@/lib/api";

/**
 * POST /api/blockchain/anchor  — anchor this image's evidence fingerprint on
 * EvidenceRegistry.sol (consent-gated). Multipart upload: field `file`,
 * optional `contract`, `rpc`, `key`, `registry`, `outDir`.
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  return handleFileCmd(
    req,
    "blockchain",
    (form, image) => {
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
    },
    { timeoutMs: 120_000 },
  );
}
