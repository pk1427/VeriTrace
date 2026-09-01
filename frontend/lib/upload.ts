import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

export interface SavedUpload {
  path: string;
  dir: string;
}

/** Persist an uploaded `File` to a fresh temp directory and return its path. */
export async function saveUpload(
  file: File,
  prefix = "veritrace",
): Promise<SavedUpload> {
  const buf = Buffer.from(await file.arrayBuffer());
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), `${prefix}-`));
  const ext = path.extname(file.name || "") || ".bin";
  const filePath = path.join(dir, `${prefix}${ext}`);
  await fs.writeFile(filePath, buf);
  return { path: filePath, dir };
}

export async function cleanupUpload(s: SavedUpload): Promise<void> {
  try {
    await fs.rm(s.dir, { recursive: true, force: true });
  } catch {
    /* ignore — temp dir best-effort cleanup */
  }
}
