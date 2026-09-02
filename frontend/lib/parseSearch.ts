/**
 * Parse the human-readable `[veritrace]` lines from the `search` subcommand
 * stdout into structured Candidate[] rows. Falls back gracefully when a line
 * is missing or has unexpected shape.
 *
 * The CLI emits one line per candidate, prefixed by either:
 *   `  + <host>  <url>  similarity=<n>  [VERIFIED]`
 *   `  - <host>  <url>  similarity=<n>  [rejected]`
 *   `  x <host>  <url>  ... <reason>  [rejected]`
 *   `  - <host>  <url>  ... <exc>  [rejected]`
 */

import type { CandidateStatus } from "@/lib/colors";

export interface Candidate {
  host: string;
  url: string;
  similarity?: number;
  reason?: string;
  status: CandidateStatus;
}

const VERIFIED_RE = /^\s*\+\s+(\S+)\s+(\S+)\s+similarity=(-?\d+\.\d+)\s+\[VERIFIED\]/;
const REJECTED_SIM_RE = /^\s*-\s+(\S+)\s+(\S+)\s+similarity=(-?\d+\.\d+)\s+\[rejected\]/;
const REJECTED_OTHER_RE = /^\s*[x\-]\s+(\S+)\s+(\S+)\s+\.\.\.\s+(.+?)\s+\[rejected\]/;

function classifyReason(reason: string): CandidateStatus {
  const r = reason.toLowerCase();
  if (r.includes("unreachable") || r.includes("urlerror") || r.includes("timeout") || r.includes("nodename")) {
    return "rejected-network";
  }
  if (r.includes("no <img>") || r.includes("page parse") || r.includes("non-image content")) {
    return "rejected-parse";
  }
  if (r.includes("no face")) {
    return "rejected-no-face";
  }
  if (r.includes("similarity") || r.includes("below threshold") || r.includes("<")) {
    return "rejected-similarity";
  }
  return "rejected-other";
}

export function parseSearchOutput(stdout: string): Candidate[] {
  if (!stdout) return [];
  const out: Candidate[] = [];
  for (const line of stdout.split(/\r?\n/)) {
    let m: RegExpMatchArray | null;
    if ((m = line.match(VERIFIED_RE))) {
      out.push({
        host: m[1]!,
        url: m[2]!,
        similarity: Number(m[3]),
        status: "verified",
      });
      continue;
    }
    if ((m = line.match(REJECTED_SIM_RE))) {
      out.push({
        host: m[1]!,
        url: m[2]!,
        similarity: Number(m[3]),
        reason: `similarity ${m[3]} < 0.6`,
        status: "rejected-similarity",
      });
      continue;
    }
    if ((m = line.match(REJECTED_OTHER_RE))) {
      const reason = m[3]!.trim();
      out.push({
        host: m[1]!,
        url: m[2]!,
        reason,
        status: classifyReason(reason),
      });
      continue;
    }
  }
  return out;
}

export interface SearchSummary {
  rawCount?: number;
  candidateCount?: number;
  verifiedCount?: number;
  rejectedCount?: number;
}

export function parseSearchSummary(stdout: string): SearchSummary {
  const out: SearchSummary = {};
  const raw = stdout.match(/vision\s*:\s*(\d+)\s+raw/);
  if (raw) out.rawCount = Number(raw[1]);
  const cand = stdout.match(/candidates\s*:\s*(\d+)\s+after/);
  if (cand) out.candidateCount = Number(cand[1]);
  const result = stdout.match(/result\s*:\s*(\d+)\s+verified\s+match\(es\)/);
  if (result) {
    out.verifiedCount = Number(result[1]);
    const rej = stdout.match(/,\s*(\d+)\s+rejected/);
    if (rej) out.rejectedCount = Number(rej[1]);
  }
  return out;
}
