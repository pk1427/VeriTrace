/**
 * Centralized color tokens for VeriTrace UI. Used by status pills, candidate
 * rows, and the chain badge so the recording reads clearly at any zoom level.
 */

export const COLORS = {
  green: "#16a34a",
  yellow: "#ca8a04",
  orange: "#ea580c",
  purple: "#7c3aed",
  red: "#dc2626",
  grey: "#71717a",
} as const;

export type ColorName = keyof typeof COLORS;

export type CandidateStatus =
  | "verified"
  | "rejected-similarity"
  | "rejected-network"
  | "rejected-parse"
  | "rejected-no-face"
  | "rejected-other";

export const STATUS_COLOR: Record<CandidateStatus, ColorName> = {
  verified: "green",
  "rejected-similarity": "yellow",
  "rejected-network": "orange",
  "rejected-parse": "grey",
  "rejected-no-face": "grey",
  "rejected-other": "grey",
};

export const STATUS_LABEL: Record<CandidateStatus, string> = {
  verified: "VERIFIED",
  "rejected-similarity": "rejected · below threshold",
  "rejected-network": "rejected · network failure",
  "rejected-parse": "rejected · page parse",
  "rejected-no-face": "rejected · no face in fetched image",
  "rejected-other": "rejected",
};

export function colorHex(name: ColorName): string {
  return COLORS[name];
}

/** Tailwind classes for a status pill (border + tinted bg + text). */
export function pillClasses(name: ColorName): string {
  const map: Record<ColorName, string> = {
    green: "border-green-600/40 bg-green-50 text-green-700",
    yellow: "border-yellow-600/40 bg-yellow-50 text-yellow-800",
    orange: "border-orange-600/40 bg-orange-50 text-orange-700",
    purple: "border-purple-600/40 bg-purple-50 text-purple-700",
    red: "border-red-600/40 bg-red-50 text-red-700",
    grey: "border-zinc-300 bg-zinc-50 text-zinc-600",
  };
  return `inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${map[name]}`;
}
