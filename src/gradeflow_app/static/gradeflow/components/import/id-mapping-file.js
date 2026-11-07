// Mapping a submissions file's student IDs to others from a CSV, before they're encrypted.

import { decryptFile, encryptFile } from "../../lib/assessment-crypto.js";
import { normalizedId } from "../../lib/cells.js";
import { detectMappingColumns, idMap, splitHeader } from "../../lib/id-mapping.js";
import { counted, formatList } from "../../lib/words.js";
import { readCsv } from "./spreadsheet-source.js";

const MAX_UNMAPPED_IDS_SHOWN = 20;

/** The ID mapping of a submissions file whose IDs `fileIds()` gives. */
export function idMappingFile(fileIds) {
  // Kept out of Alpine's proxies: the mapping file's bytes, or the server's encrypted copy.
  let contents = null;

  return {
    /** The mapping file's name, header, rows, chosen columns and the map they make, frozen to keep it out of Alpine's
     * proxies; null without one. */
    mapping: null,

    mappedId(id) {
      return this.mapping?.map.get(normalizedId(id)) ?? id;
    },
    get unmappedIds() {
      return this.mapping ? fileIds().filter((id) => !this.mapping.map.has(normalizedId(id))) : [];
    },
    get note() {
      return this.mapping ? `IDs from ${this.mapping.fileName}` : "";
    },
    get rowCount() {
      return counted(this.mapping?.rows.length ?? 0, "row");
    },
    get summary() {
      if (!this.mapping) return "";
      const unmapped = this.unmappedIds;
      const foundText = `${fileIds().length - unmapped.length} found`;
      if (!unmapped.length) return foundText;
      const hidden = unmapped.length - MAX_UNMAPPED_IDS_SHOWN;
      const idsToShow = hidden > 0 ? [...unmapped.slice(0, MAX_UNMAPPED_IDS_SHOWN), `${hidden} more`] : unmapped;
      return `${foundText}. Not found, so kept as they are: ${formatList(idsToShow)}`;
    },
    get example() {
      const id = fileIds().find((fileId) => this.mappedId(fileId) !== fileId);
      return id ? `${id} → ${this.mappedId(id)}` : "";
    },
    /** The chosen columns, as the upload saves them. */
    get columns() {
      return (
        this.mapping && {
          file_name: this.mapping.fileName,
          match_column: this.mapping.matchColumn,
          id_column: this.mapping.idColumn,
        }
      );
    },

    async open(file) {
      const bytes = await file.bytes();
      await this.read(file.name, bytes);
      contents = { bytes };
    },
    /** Opens the saved mapping file from the server's encrypted copy. */
    async reopen(encrypted, keys, savedColumns) {
      await this.read(savedColumns.file_name, await decryptFile(encrypted, keys), savedColumns);
      contents = { encrypted };
    },
    async read(fileName, bytes, savedColumns = null) {
      const { header, rows } = splitHeader(await readCsv(bytes));
      if (!header.length) throw new Error("no rows");
      const detected = detectMappingColumns(header, rows, fileIds());
      const matchColumn = savedColumns?.match_column ?? (detected.matchColumn || header[0]);
      const idColumn = savedColumns?.id_column ?? (detected.idColumn || header[1] || header[0]);
      this.setColumns({ fileName, header, rows, matchColumn, idColumn });
    },
    setColumn(event) {
      this.setColumns({ ...this.mapping, [event.target.dataset.column]: event.target.value });
    },
    setColumns(mapping) {
      this.mapping = Object.freeze({
        ...mapping,
        map: idMap(mapping.header, mapping.rows, mapping.matchColumn, mapping.idColumn),
      });
    },
    remove() {
      this.mapping = null;
      contents = null;
    },
    /** The columns and the encrypted file, as upload form fields; none without a mapping. */
    async formFields(keys) {
      if (!this.mapping) return {};
      const encrypted = contents.encrypted ?? (await encryptFile(contents.bytes, keys));
      return { id_mapping: JSON.stringify(this.columns), id_mapping_file: new Blob([encrypted]) };
    },
  };
}
