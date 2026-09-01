export type VeriTraceCmd = "scan" | "check" | "enroll" | "search" | "evidence";

export interface VeriTraceResult {
  ok: boolean;
  stdout: string;
  stderr: string;
  code: number | null;
  durationMs: number;
}

export interface ScanSummary {
  backend?: string;
  bbox?: {
    x1: number;
    y1: number;
    x2: number;
    y2: number;
    width: number;
    height: number;
  };
  embeddingDim?: number;
  embeddingNorm?: number;
  status?: string;
}

export interface ApiResponse<T = unknown> {
  ok: boolean;
  command?: string;
  summary?: T;
  raw?: VeriTraceResult;
  error?: string;
}
