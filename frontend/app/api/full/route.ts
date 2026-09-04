import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { cleanupUpload, saveUpload, type SavedUpload } from "@/lib/upload";
import { DEMO_REGISTRY_RELATIVE_PATH } from "@/lib/demo";
import { runVeriTraceCmd } from "@/lib/runPythonCmd";

/**
 * Runs the judge-facing pipeline in one server-side command. Credentials stay
 * in the git-ignored VeriTrace .env; the browser receives only command output.
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  let saved: SavedUpload | undefined;
  try {
    const form = await req.formData();
    const file = form.get("file");
    if (!file || typeof file === "string") {
      return NextResponse.json({ ok: false, error: "missing face image" }, { status: 400 });
    }
    saved = await saveUpload(file as File);
    const result = await runVeriTraceCmd("full", [
      saved.path,
      "--registry", DEMO_REGISTRY_RELATIVE_PATH,
      "--providers", "vision_web_detection",
      "--max-results", "10",
      // PublicNode is used for the recording because the configured dRPC
      // endpoint may temporarily rate-limit or return HTTP 500.
      "--rpc", "https://polygon-amoy-bor-rpc.publicnode.com",
    ], { timeoutMs: 240_000 });
    return NextResponse.json({ ok: result.ok, raw: result });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json({ ok: false, error: message }, { status: 500 });
  } finally {
    if (saved) await cleanupUpload(saved);
  }
}
