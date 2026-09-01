import { NextRequest, NextResponse } from "next/server";

import { parseScanOutput } from "@/lib/parse";
import { saveUpload, cleanupUpload, type SavedUpload } from "@/lib/upload";
import { runVeriTraceCmd } from "@/lib/runPythonCmd";

/**
 * POST /api/scan  — read-only Phase 1 scan (detect face + 512-d embedding).
 * Multipart upload: field `file` (any image).
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  let saved: SavedUpload | undefined;
  try {
    const form = await req.formData();
    const file = form.get("file");
    if (!file || typeof file === "string") {
      return NextResponse.json(
        { ok: false, command: "scan", error: "missing 'file' upload" },
        { status: 400 },
      );
    }
    saved = await saveUpload(file as File);
    const res = await runVeriTraceCmd("scan", [saved.path], { timeoutMs: 90_000 });
    return NextResponse.json({
      ok: res.ok,
      command: "scan",
      summary: parseScanOutput(res.stdout) || undefined,
      raw: res,
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      { ok: false, command: "scan", error: "unexpected server error: " + msg },
      { status: 500 },
    );
  } finally {
    if (saved) await cleanupUpload(saved);
  }
}
