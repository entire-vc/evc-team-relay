/* Check a generated Python CRDT update against the actual plugin rename observer.
 * node scripts/check_relocation_folder_index.cjs /path/to/plugin /path/to/fixtures
 * Fixtures: before.yjs and move.yjs from test_relocate_artifacts.py.
 * This proves aliases, not a full Obsidian filesystem acceptance test.
 */
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
if (process.argv.length !== 4) {
  console.error('Usage: node check_relocation_folder_index.cjs <plugin> <fixtures>');
  process.exit(2);
}
const plugin = path.resolve(process.argv[2]);
const fixtures = path.resolve(process.argv[3]);
const esbuild = require(path.join(plugin, 'node_modules/esbuild'));
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'relocation-folder-index-'));
(async () => { try {
  fs.writeFileSync(path.join(tmp, 'logging.ts'), `
    export class Loggable { log() {} debug() {} warn() {} error() {} }
    export const instanceLabels = new WeakMap();
    export const namedLogger = () => () => {};
  `);
  fs.writeFileSync(path.join(tmp, 'features.ts'), `
    export const currentToggles = () => ({});
    export const withToggle = () => {};
  `);
  const output = path.join(tmp, 'check.cjs');
  await esbuild.build({
    stdin: {contents: `
      import * as Y from ${JSON.stringify(path.join(plugin, 'node_modules/yjs'))};
      import { FolderIndex } from ${JSON.stringify(path.join(plugin, 'src/FolderIndex.ts'))};
      import fs from 'node:fs';
      import assert from 'node:assert/strict';
      const doc = new Y.Doc();
      Y.applyUpdate(doc, fs.readFileSync(${JSON.stringify(path.join(fixtures, 'before.yjs'))}));
      const records = doc.getMap('filemeta_v0');
      const context = { records, aliases: new Map(), log() {} };
      records.observe(event => FolderIndex.prototype.applyRemoteChange.call(context, event));
      Y.applyUpdate(doc, fs.readFileSync(${JSON.stringify(path.join(fixtures, 'move.yjs'))}));
      assert.equal(context.aliases.get('deadbeef'), 'artifacts/deadbeef');
      assert.equal(context.aliases.get('deadbeef/note.md'), 'artifacts/deadbeef/note.md');
      assert.equal(context.aliases.get('deadbeef/file.bin'), 'artifacts/deadbeef/file.bin');
      assert.equal(records.get('artifacts/deadbeef').id, 'folder-id');
      assert.equal(records.has('deadbeef'), false);
      assert.equal(doc.getMap('docs').get('artifacts/deadbeef/note.md'), 'note-id');
      console.log('PASS: actual FolderIndex observer pairs folder ID and creates all child aliases');
    `, resolveDir: plugin},
    bundle: true, platform: 'node', format: 'cjs', outfile: output,
    plugins: [{name: 'host-stubs', setup(build) {
      build.onResolve({filter: /(?:^|\/)logging$/}, () => ({path: path.join(tmp, 'logging.ts')}));
      build.onResolve({filter: /(?:^|\/)featureToggleState$/}, () => ({path: path.join(tmp, 'features.ts')}));
    }}],
  });
  require(output);
} finally {
  fs.rmSync(tmp, {recursive: true, force: true});
}})().catch(error => { console.error(error); process.exitCode = 1; });
