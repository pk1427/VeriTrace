import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { handleFileCmd } from "@/lib/api";

/**
 * POST /api/consent-check  — Phase 2 hard consent gate (read-only policy
 * decision; compares an uploaded face against enrolled owners). Multipart
 * upload: field `file`.
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  return handleFileCmd(req, "check", (_form, image) => [image], {
    timeoutMs: 90_000,
  });
}
