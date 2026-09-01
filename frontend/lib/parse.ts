import type { ScanSummary } from "@/lib/types";

/**
 * Parse the human-readable `[veritrace]` lines emitted by `scan` into a
 * structured summary. Falls back to null fields when a line is absent.
 */
export function parseScanOutput(stdout: string): ScanSummary | null {
  if (!stdout) return null;
  const out: ScanSummary = {};

  const backendMatch = stdout.match(/backend\s*:\s*(.+)/);
  if (backendMatch) out.backend = backendMatch[1].trim();

  const bboxMatch = stdout.match(
    /bbox\s*:\s*\((\d+),\s*(\d+)\)\s*->\s*\((\d+),\s*(\d+)\)\s*width=(\d+)\s*height=(\d+)/,
  );
  if (bboxMatch) {
    out.bbox = {
      x1: Number(bboxMatch[1]),
      y1: Number(bboxMatch[2]),
      x2: Number(bboxMatch[3]),
      y2: Number(bboxMatch[4]),
      width: Number(bboxMatch[5]),
      height: Number(bboxMatch[6]),
    };
  }

  const embMatch = stdout.match(/embedding\s*:\s*dim=(\d+)\s*norm=([\d.]+)/);
  if (embMatch) {
    out.embeddingDim = Number(embMatch[1]);
    out.embeddingNorm = Number(embMatch[2]);
  }

  const statusMatch = stdout.match(/status\s*:\s*(\w+)/);
  if (statusMatch) out.status = statusMatch[1].trim();

  return out;
}
