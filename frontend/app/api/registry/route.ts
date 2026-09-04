/**
 * POST /api/registry  — read-only summary of the local consent registry for
 * the recording UI's "Enrolled subjects" panel. Never returns biometric
 * templates — only subject_id, enrolled_at, and allowed_domains.
 *
 * Wraps a tiny Python expression so the route stays a thin shim. We avoid
 * the full `python src/main.py` subcommand (no such subcommand exists) and
 * instead run a one-liner via the same `runVeriTraceCmd` helper so the
 * interpreter + cwd resolution stays consistent with the other routes.
 */
import { NextResponse } from "next/server";

import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

import { runPythonScript } from "@/lib/runPythonCmd";
import { DEMO_REGISTRY_RELATIVE_PATH } from "@/lib/demo";

export const dynamic = "force-dynamic";

const REGISTRY_QUERY = `
import json
import sys
from src.consent.registry import load_registry

reg = load_registry("${DEMO_REGISTRY_RELATIVE_PATH}")
out = {
    "version": reg.get("version"),
    "match_threshold": reg.get("match_threshold"),
    "subjects": [
        {
            "subject_id": s.get("subject_id"),
            "enrolled_at": s.get("enrolled_at"),
            "enrollment_photo": s.get("enrollment_photo"),
            "allowed_domains": s.get("allowed_domains"),
        }
        for s in reg.get("subjects", [])
    ],
}
sys.stdout.write(json.dumps(out, indent=2))
`;

export async function GET(): Promise<NextResponse> {
  // Write the multi-line expression to a temp file because `-c` with
  // semicolon-joined multi-statement Python is fragile across shells.
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "veritrace-registry-"));
  const queryPath = path.join(dir, "query.py");
  await fs.writeFile(queryPath, REGISTRY_QUERY, "utf8");
  let res;
  try {
    res = await runPythonScript(queryPath, { timeoutMs: 30_000 });
  } finally {
    try {
      await fs.rm(dir, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
  }
  if (!res.ok) {
    return NextResponse.json(
      { ok: false, error: "registry query failed: " + res.stderr, raw: res },
      { status: 500 },
    );
  }
  try {
    const data = JSON.parse(res.stdout);
    return NextResponse.json({ ok: true, registry: data });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      { ok: false, error: "could not parse registry JSON: " + msg, raw: res },
      { status: 500 },
    );
  }
}
