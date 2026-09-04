import { promises as fs } from "node:fs";
import path from "node:path";

import { NextResponse } from "next/server";

import { DEMO_REGISTRY_RELATIVE_PATH, EMPTY_DEMO_REGISTRY } from "@/lib/demo";
import { REPO_ROOT } from "@/lib/runPythonCmd";

/** Reset only the disposable, local recording registry. */
export async function POST(): Promise<NextResponse> {
  const target = path.join(REPO_ROOT, DEMO_REGISTRY_RELATIVE_PATH);
  try {
    await fs.mkdir(path.dirname(target), { recursive: true });
    await fs.writeFile(target, JSON.stringify(EMPTY_DEMO_REGISTRY, null, 2), "utf8");
    return NextResponse.json({ ok: true, message: "Fresh local demo registry created." });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json({ ok: false, error: message }, { status: 500 });
  }
}
