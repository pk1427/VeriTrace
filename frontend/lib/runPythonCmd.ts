import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import type { VeriTraceResult } from "@/lib/types";

/**
 * Root of the VeriTrace repo (parent of the Next.js `frontend/` dir when run
 * via `npm run dev` from `frontend/`). Override with VERIFACE_REPO_ROOT.
 */
export const REPO_ROOT: string =
  process.env.VERIFACE_REPO_ROOT || path.resolve(process.cwd(), "..");

/** Resolve the Python interpreter to use: env > venv > system. */
export function resolvePython(): string {
  const envBin = process.env.VERIFACE_PYTHON;
  if (envBin) {
    return envBin;
  }
  const venvPy = path.join(REPO_ROOT, ".venv", "bin", "python");
  if (fs.existsSync(venvPy)) {
    return venvPy;
  }
  return process.platform === "win32" ? "python" : "python3";
}

export function veritraceCliPath(): string {
  return path.join(REPO_ROOT, "src", "main.py");
}

/**
 * Run a `python src/main.py <command> <args...>` subprocess and capture its
 * output. The child runs with cwd=REPO_ROOT so relative image paths resolve.
 * Resolves (never rejects) so API routes can return structured errors.
 */
export function runVeriTraceCmd(
  command: string,
  args: string[],
  opts?: { timeoutMs?: number },
): Promise<VeriTraceResult> {
  const pyBin = resolvePython();
  const cli = veritraceCliPath();
  const argv = [cli, command, ...args];
  const timeoutMs = opts?.timeoutMs ?? 120_000;
  const start = Date.now();

  return new Promise((resolve) => {
    const child = spawn(pyBin, argv, {
      cwd: REPO_ROOT,
      env: { ...process.env },
      windowsHide: true,
    });

    let stdout = "";
    let stderr = "";
    let settled = false;

    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      try {
        child.kill("SIGTERM");
      } catch {
        /* ignore */
      }
      resolve({
        ok: false,
        stdout,
        stderr: (stderr + "\n[veritrace] timeout after " + timeoutMs + "ms").trim(),
        code: null,
        durationMs: Date.now() - start,
      });
    }, timeoutMs);

    if (child.stdout) {
      child.stdout.on("data", (d: Buffer) => {
        stdout += d.toString();
      });
    }
    if (child.stderr) {
      child.stderr.on("data", (d: Buffer) => {
        stderr += d.toString();
      });
    }

    child.on("error", (err: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({
        ok: false,
        stdout,
        stderr: (stderr + "\n" + err.message).trim(),
        code: null,
        durationMs: Date.now() - start,
      });
    });

    child.on("close", (code: number | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({
        ok: code === 0,
        stdout,
        stderr,
        code,
        durationMs: Date.now() - start,
      });
    });
  });
}
