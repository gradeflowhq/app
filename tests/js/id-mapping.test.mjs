import assert from 'node:assert/strict';
import { test } from 'node:test';

import { detectMappingColumns, idMap, splitHeader } from '../../src/gradeflow_app/static/gradeflow/id-mapping.js';

const { header, rows } = splitHeader([
  ['plab ID', 'Name', 'Email'],
  ['plab1001', 'Ann Tan', 'ann@example.edu'],
  [''],
  ['PLAB1002', 'Ben Lim', 'ben@example.edu'],
  ['plab1003', 'Cal Ng', ''],
]);

test('the table is the first row as the header and the rows with values', () => {
  assert.deepEqual(header, ['plab ID', 'Name', 'Email']);
  assert.equal(rows.length, 3);
});

test('the IDs are matched in the column holding most of them, and replaced with the best ID column of the rest', () => {
  assert.deepEqual(detectMappingColumns(header, rows, ['plab1001', 'plab1002', 'plab9999']), { matchColumn: 'plab ID', idColumn: 'Email' });
});

test('a Canvas gradebook maps Canvas user IDs to SIS user IDs', () => {
  const gradebook = splitHeader([
    ['Student', 'ID', 'SIS User ID', 'SIS Login ID', 'Section'],
    ['Tan, Ann', '48213', '20260001', 'ann@example.edu', 'A'],
    ['Lim, Ben', '50121', '20260002', 'ben@example.edu', 'A'],
  ]);
  assert.deepEqual(detectMappingColumns(gradebook.header, gradebook.rows, ['48213', '50121']), { matchColumn: 'ID', idColumn: 'SIS User ID' });
});

test('no column holding the IDs matches nothing', () => {
  assert.deepEqual(detectMappingColumns(header, rows, ['x1']), { matchColumn: '', idColumn: '' });
});

test('IDs map ignoring case and spaces; blank replacements are left out', () => {
  assert.deepEqual(idMap(header, rows, 'plab ID', 'Email'), new Map([['plab1001', 'ann@example.edu'], ['plab1002', 'ben@example.edu']]));
});

test('a Moodle grading worksheet matches participant numbers in its Identifier column', () => {
  const worksheet = splitHeader([
    ['Identifier', 'Full name', 'Email address', 'Status'],
    ['Participant 1234567', 'Ann Tan', 'ann@example.edu', 'Submitted'],
    ['Participant 1234568', 'Ben Lim', 'ben@example.edu', 'Submitted'],
  ]);
  const { matchColumn, idColumn } = detectMappingColumns(worksheet.header, worksheet.rows, ['1234567', '1234568']);
  assert.deepEqual({ matchColumn, idColumn }, { matchColumn: 'Identifier', idColumn: 'Email address' });
  assert.equal(idMap(worksheet.header, worksheet.rows, matchColumn, idColumn).get('1234567'), 'ann@example.edu');
});
