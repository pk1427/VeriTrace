import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { handleFileCmd } from "@/lib/api";

/**
 * POST /api/blockchain/verify  — independently re-verify an evidence
 * fingerprint on-chain (consent-gated). Multipart upload: field `file`,
 * optional `contract`, `rpc`, `registry`.
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  return handleFileCmd(
    req,
    "blockchain",
    (form, image) => {
      const args: string[] = ["verify", image];
      const contract = String(form.get("contract") ?? "").trim();
      if (contract) args.push("--contract", contract);
      const rpc = String(form.get("rpc") ?? "").trim();
      if (rpc) args.push("--rpc", rpc);
      const registry = String(form.get("registry") ?? "").trim();
      if (registry) args.push("--registry", registry);
      return args;
    },
    { timeoutMs: 120_000 },
  );
}
