/**
 * Client-side helper for the tamper demo. Reads the original file, flips the
 * last byte (after the 16-byte signature byte range to keep JPEG headers
 * intact for the file viewer), and returns a new File the UI can upload as
 * a "tampered" copy. SHA-256 will differ even though the image still renders.
 */
export async function tamperFile(original: File): Promise<File> {
  const buf = new Uint8Array(await original.arrayBuffer());
  if (buf.length < 2) {
    throw new Error("file is too small to tamper");
  }
  const out = new Uint8Array(buf);
  // Flip the last byte. For JPEGs this is past the EOI marker, so the image
  // still renders in browsers; the SHA-256 is guaranteed to differ.
  out[out.length - 1] = out[out.length - 1]! ^ 0x01;
  const name = `tampered_${original.name || "photo"}`;
  return new File([out], name, { type: original.type || "application/octet-stream" });
}
