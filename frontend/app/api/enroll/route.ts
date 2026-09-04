import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { handleFileCmd } from "@/lib/api";
import { DEMO_REGISTRY_RELATIVE_PATH } from "@/lib/demo";

/**
 * POST /api/enroll  — enroll an owner-owned face into the local consent
 * registry (Phase 2). Multipart upload: field `file`, plus `subject` (name id),
 * `allowedDomains` (comma-separated), optional `registry`.
 *
 * The route is wired and ready; enrollment is the owner's explicit action.
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  return handleFileCmd(
    req,
    "enroll",
    (form, image) => {
      const subject = String(form.get("subject") ?? "").trim();
      if (!subject) {
        throw new Error("missing 'subject' field");
      }
      const args: string[] = [subject, image];
      const domains = String(form.get("allowedDomains") ?? "").trim();
      if (domains) {
        args.push("--allowed-domains", domains);
      }
      const registry = String(form.get("registry") ?? "").trim();
      args.push("--registry", registry || DEMO_REGISTRY_RELATIVE_PATH);
      return args;
    },
    { timeoutMs: 180_000 },
  );
}
