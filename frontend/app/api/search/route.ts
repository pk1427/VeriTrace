import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { handleFileCmd } from "@/lib/api";

/**
 * POST /api/search  — consent-gated reverse-image search of approved domains
 * (Phase 2b). The consent gate runs inside the Python `search` subcommand
 * (refuses unenrolled faces before calling Vision). Multipart upload: field
 * `file`, optional `maxResults`, `threshold`, `registry`.
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  return handleFileCmd(
    req,
    "search",
    (form, image) => {
      const args: string[] = [image];
      const mr = String(form.get("maxResults") ?? "").trim();
      if (mr) args.push("--max-results", mr);
      const threshold = String(form.get("threshold") ?? "").trim();
      if (threshold) args.push("--threshold", threshold);
      const registry = String(form.get("registry") ?? "").trim();
      if (registry) args.push("--registry", registry);
      return args;
    },
    { timeoutMs: 180_000 },
  );
}
