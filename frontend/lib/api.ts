import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { saveUpload, cleanupUpload, type SavedUpload } from "@/lib/upload";
import { runVeriTraceCmd } from "@/lib/runPythonCmd";
import type { VeriTraceResult } from "@/lib/types";

/**
 * Shared helper for API routes that run a VeriTrace CLI command against an
 * uploaded file. Parses multipart `formData` (field `file`), writes the upload
 * to a temp dir, invokes `python src/main.py <command> <args...>`, cleans up the
 * temp dir, and returns the structured result.
 *
 * `buildArgs` receives the parsed form fields + the temp image path and returns
 * the full argv (positional + flags). If `buildArgs` throws, the error is
 * surfaced as HTTP 400 (treat as a client/validation error).
 */
export async function handleFileCmd(
  req: NextRequest,
  command: string,
  buildArgs: (form: FormData, imagePath: string) => string[],
  opts?: { timeoutMs?: number },
): Promise<NextResponse> {
  let saved: SavedUpload | undefined;
  let form: FormData;
  try {
    form = await req.formData();
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      { ok: false, command, error: "could not parse multipart body: " + msg },
      { status: 400 },
    );
  }

  const file = form.get("file");
  if (!file || typeof file === "string") {
    return NextResponse.json(
      {
        ok: false,
        command,
        error: "missing 'file' upload (multipart/form-data, field='file', type='file')",
      },
      { status: 400 },
    );
  }

  let savedResult: VeriTraceResult;
  try {
    saved = await saveUpload(file as File);
    let args: string[];
    try {
      args = buildArgs(form, saved.path);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      return NextResponse.json(
        { ok: false, command, error: msg },
        { status: 400 },
      );
    }
    savedResult = await runVeriTraceCmd(command, args, opts);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      { ok: false, command, error: "unexpected server error: " + msg },
      { status: 500 },
    );
  } finally {
    if (saved) {
      await cleanupUpload(saved);
    }
  }

  return NextResponse.json({
    ok: savedResult!.ok,
    command,
    raw: savedResult!,
  });
}
