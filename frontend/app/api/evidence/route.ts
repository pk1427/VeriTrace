import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { handleFileCmd } from "@/lib/api";

/**
 * POST /api/evidence  — fingerprint an image into tamper-evident SHA-256
 * evidence (consent-gated). Multipart upload: field `file`, optional
 * `registry`, `outDir`.
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  return handleFileCmd(
    req,
    "evidence",
    (form, image) => {
      const args: string[] = [image];
      const registry = String(form.get("registry") ?? "").trim();
      if (registry) args.push("--registry", registry);
      const outDir = String(form.get("outDir") ?? "").trim();
      if (outDir) args.push("--out-dir", outDir);
      return args;
    },
    { timeoutMs: 120_000 },
  );
}
