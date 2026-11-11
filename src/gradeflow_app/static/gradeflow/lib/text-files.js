// The text of an uploaded file, whatever program saved it.

const BINARY_PROBE_BYTES = 8000;

/** A file's text, read as UTF-8 or else Windows-1252, with Unix line endings; null for a binary file. */
export function decodeText(bytes) {
  if (bytes.subarray(0, BINARY_PROBE_BYTES).includes(0)) return null;
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    text = new TextDecoder("windows-1252").decode(bytes);
  }
  return text.replaceAll("\r\n", "\n");
}
