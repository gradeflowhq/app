import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  answerFromFiles,
  decodeText,
  fileQuestionsFor,
  filesForQuestion,
  isIgnoredPath,
  matchPaths,
  pathTokens,
  patternFromTokens,
  patternRegex,
  samplePaths,
  suggestPattern,
  topLevelEntries,
} from '../../src/gradeflow_app/static/gradeflow/zip-files.js';

const PE = [
  'pe_submissions/plab1001/PE_1A.py',
  'pe_submissions/plab1001/PE_1B.py',
  'pe_submissions/plab1002/PE_1A.py',
  'pe_submissions/plab1002/PE.1B.py',
  'pe_submissions/plab1003/PE/PE_1A.py',
  'pe_submissions/plab1003/PE/PE_1B.py',
  'pe_submissions/README.txt',
];

const CANVAS = [
  'tanann_48213_7712345_game.py',
  'tanann_48213_7712345_board.py',
  'limben_late_50121_7712399_game.py',
  'limben_late_50121_7712399_board.py',
  'ngcal_61002_7712402_game-1.py',
];

test('system files, folders and macOS metadata are ignored', () => {
  for (const path of ['__MACOSX/a/._x.py', 'a/.DS_Store', 'a/__pycache__/x.pyc', 'a/.git/HEAD', 'a/b/']) assert.ok(isIgnoredPath(path), path);
  assert.ok(!isIgnoredPath('a/b/x.py'));
});

test('text is UTF-8, else Windows-1252; NUL bytes mean binary', () => {
  assert.equal(decodeText(new TextEncoder().encode('﻿print("é")\r\n')), 'print("é")\n');
  assert.equal(decodeText(Uint8Array.of(0x23, 0x20, 0xe9, 0x0a)), '# é\n');
  assert.equal(decodeText(Uint8Array.of(0x89, 0x50, 0x00, 0x01)), null);
});

test('a path splits into tokens and separators', () => {
  assert.deepEqual(
    pathTokens('a_b/c.py').map(({ text, isSeparator }) => [text, isSeparator]),
    [['a', false], ['_', true], ['b', false], ['/', true], ['c.py', false]],
  );
});

test('a folder per student is suggested from the part that varies', () => {
  assert.equal(suggestPattern(PE), 'pe_submissions/{student}/{file}');
});

test('flat file names: the student is the first varying number, and the next number is skipped', () => {
  assert.equal(suggestPattern(CANVAS), '*_{student:number}_{number}_{file}');
});

test('a student and file start on a sample path make a pattern: shared text stays, varying text becomes *', () => {
  const tokens = pathTokens(CANVAS[0]);
  assert.equal(patternFromTokens(tokens, 2, 6, CANVAS), '*_{student:number}_{number}_{file}');
  assert.equal(patternFromTokens(pathTokens(PE[0]), 4, 6, PE), 'pe_submissions/{student}/{file}');
  assert.equal(patternFromTokens(pathTokens('a/s1/x_y.py'), 2, 6, ['a/s1/x_y.py', 'a/s2/x_y.py']), 'a/{student}/x_{file}');
  assert.equal(patternFromTokens(pathTokens(PE[0]), 2, 6, PE), 'pe_{student}/*/{file}');
});

test('a pattern needs one student and ends with the file', () => {
  assert.throws(() => patternRegex('{student:id}/{file}'), /\{student\} or \{student:number\}/);
  assert.throws(() => patternRegex('{file}'), /\{student\}/);
  assert.throws(() => patternRegex('{student}/{file}/x'), /\{file\}/);
  assert.throws(() => patternRegex('{student}_{student}/{file}'), /once/);
});

test('matching finds each student’s files and lists those that don’t fit', () => {
  const { submissions, unmatchedPaths } = matchPaths(patternRegex('pe_submissions/{student}/{file}'), PE);
  assert.deepEqual([...submissions.keys()], ['plab1001', 'plab1002', 'plab1003']);
  assert.deepEqual(submissions.get('plab1003'), [
    { file: 'PE/PE_1A.py', path: 'pe_submissions/plab1003/PE/PE_1A.py' },
    { file: 'PE/PE_1B.py', path: 'pe_submissions/plab1003/PE/PE_1B.py' },
  ]);
  assert.deepEqual(unmatchedPaths, ['pe_submissions/README.txt']);
});

test('a late submission’s extra part is absorbed by *', () => {
  const { submissions, unmatchedPaths } = matchPaths(patternRegex('*_{student:number}_{number}_{file}'), CANVAS);
  assert.deepEqual([...submissions.keys()], ['48213', '50121', '61002']);
  assert.deepEqual(unmatchedPaths, []);
});

test('a text student matches a whole folder name', () => {
  const { submissions } = matchPaths(patternRegex('{student}/{file}'), ['tan_ann/a.py', 'lim ben/b.py']);
  assert.deepEqual([...submissions.keys()], ['tan_ann', 'lim ben']);
});

test('top-level entries are files and folders; a lone wrapping folder is unwrapped', () => {
  const submissions = new Map([
    ['u1', [{ file: 'PE/PE_1A.py', path: 'u1/PE/PE_1A.py' }, { file: 'PE/q4/main.py', path: 'u1/PE/q4/main.py' }, { file: 'PE/q4/util.py', path: 'u1/PE/q4/util.py' }]],
    ['u2', [{ file: 'PE_1A.py', path: 'u2/PE_1A.py' }, { file: 'q4/main.py', path: 'u2/q4/main.py' }]],
    ['u3', [{ file: 'PE_1A.py', path: 'u3/PE_1A.py' }]],
  ]);
  const entries = topLevelEntries(submissions).get('u1');
  assert.deepEqual([...entries.keys()], ['PE_1A.py', 'q4/']);
  assert.deepEqual(entries.get('q4/').map(({ file }) => file), ['q4/main.py', 'q4/util.py']);
});

test('a lone folder most students have is an answer, not a wrapper', () => {
  const submissions = new Map(['u1', 'u2', 'u3'].map((id) => [id, [{ file: 'q4/main.py', path: `${id}/q4/main.py` }]]));
  assert.deepEqual([...topLevelEntries(submissions).get('u1').keys()], ['q4/']);
});

test('common files become questions; misspellings join them by name; the rest have none', () => {
  const counts = new Map([
    ['PE_1A.py', 10], ['PE_1B.py', 9], ['q4/', 8],
    ['PE.1A.py', 1], ['PE_1B .py.py', 1], ['test.py', 2], ['PE_1A (1).py', 1],
  ]);
  assert.deepEqual(fileQuestionsFor(counts, 10), {
    'PE_1A.py': 'PE_1A', 'PE_1B.py': 'PE_1B', 'q4/': 'q4',
    'PE.1A.py': 'PE_1A', 'PE_1B .py.py': 'PE_1B', 'PE_1A (1).py': 'PE_1A', 'test.py': '',
  });
});

test('a name is matched as written before its copy number is dropped', () => {
  const counts = new Map([['Q-1.py', 9], ['Q-2.py', 9], ['Q1.py', 1], ['game.py', 9], ['game-1.py', 1]]);
  const questions = fileQuestionsFor(counts, 10);
  assert.equal(questions['Q1.py'], 'Q-1');
  assert.equal(questions['game-1.py'], 'game');
});

test('saved questions are kept; files new to them get their defaults', () => {
  const counts = new Map([['a.py', 9], ['new.py', 8], ['x.py', 1]]);
  assert.deepEqual(fileQuestionsFor(counts, 10, { 'a.py': 'q', 'x.py': 'q', 'gone.py': 'q' }), { 'a.py': 'q', 'new.py': 'new', 'x.py': 'q' });
});

test('an answer uses the question’s common files, else its others, the file named after it first', () => {
  const entries = new Map([
    ['board.py', [{ file: 'board.py', path: 'p/board.py' }]],
    ['game.py', [{ file: 'game.py', path: 'p/game.py' }]],
    ['Game.py', [{ file: 'Game.py', path: 'p/Game.py' }]],
  ]);
  const fileQuestions = { 'board.py': 'game', 'game.py': 'game', 'Game.py': 'game' };
  const common = new Set(['game.py', 'board.py']);
  assert.deepEqual(filesForQuestion(entries, 'game', fileQuestions, common).map(({ file }) => file), ['game.py', 'board.py']);
  entries.delete('game.py');
  entries.delete('board.py');
  assert.deepEqual(filesForQuestion(entries, 'game', fileQuestions, common).map(({ file }) => file), ['Game.py']);
});

test('a folder’s files are named from inside it, main.py first, then Python, then the rest', () => {
  const entries = new Map([['q4/', ['q4/data.csv', 'q4/a.py', 'q4/lib/b.py', 'q4/main.py'].map((file) => ({ file, path: file }))]]);
  assert.deepEqual(filesForQuestion(entries, 'q4', { 'q4/': 'q4' }, new Set(['q4/'])).map(({ file }) => file), ['main.py', 'a.py', 'lib/b.py', 'data.csv']);
});

test('a folder beside other files keeps its name, so it can be imported', () => {
  const entries = new Map([['a.py', [{ file: 'a.py', path: 'a' }]], ['lib/', [{ file: 'lib/util.py', path: 'u' }]]]);
  assert.deepEqual(filesForQuestion(entries, 'a', { 'a.py': 'a', 'lib/': 'a' }, new Set(['a.py', 'lib/'])).map(({ file }) => file), ['a.py', 'lib/util.py']);
});

test('several files join as txtar; one file stays as written', () => {
  assert.equal(answerFromFiles([['a.py', 'x = 1\n'], ['b.py', 'y = 2']]), '-- a.py --\nx = 1\n-- b.py --\ny = 2\n');
  assert.equal(answerFromFiles([['a.py', 'x = 1\n']]), 'x = 1\n');
  assert.throws(() => answerFromFiles([['a.sql', 'select 1;\n-- note --\n'], ['b.py', '']]), /a\.sql line 2 looks like a file marker/);
  assert.equal(answerFromFiles([['a.sql', 'select 1;\n-- note --\n']]), 'select 1;\n-- note --\n');
  assert.throws(() => answerFromFiles([['a.sql', '-- note --\nselect 1;\n']]), /a\.sql line 1/);
});

test('sample paths cover each shape of path, the most common first', () => {
  assert.deepEqual(samplePaths(CANVAS), [CANVAS[0], CANVAS[2]]);
  assert.deepEqual(samplePaths(['a/u1/PE_1A.py', 'a/u2/PE4.py', 'a/u3/x/PE_1A.py']), ['a/u1/PE_1A.py', 'a/u3/x/PE_1A.py']);
});

test('Moodle: the participant number, then the parts every path shares', () => {
  const paths = [
    'Ann Tan_1234567_assignsubmission_file/q1.py',
    'Ann Tan_1234567_assignsubmission_file/q2.py',
    'Ben Lim_1234568_assignsubmission_file/q1.py',
  ];
  assert.equal(suggestPattern(paths), '*_{student:number}_assignsubmission_file/{file}');
});

test('Blackboard: the username after the assignment name, skipping the attempt and its time', () => {
  const paths = [
    'Lab 2_ann3_attempt_2026-03-01-10-00-00_q1.py',
    'Lab 2_ann3_attempt_2026-03-01-10-00-00.txt',
    'Lab 2_ben7_attempt_2026-03-02-11-30-00_q1.py',
    'Lab 2_cal9_attempt_2026-03-02-12-00-00_q1.py',
  ];
  const pattern = suggestPattern(paths);
  assert.equal(pattern, 'Lab 2_{student}_attempt_*_{file}');
  assert.deepEqual(matchPaths(patternRegex(pattern), paths).unmatchedPaths, ['Lab 2_ann3_attempt_2026-03-01-10-00-00.txt']);
});

