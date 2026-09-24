#!/usr/bin/env node
/**
 * Bundle-size ratchet for web-publish's client JS.
 *
 * For each route in perf/budget.json, computes the STATIC (never
 * dynamic-import) closure of client chunks SvelteKit loads just to render
 * that route — i.e. what a first-time visitor's browser fetches before any
 * user interaction — sums their real gzip size, and fails if it exceeds the
 * configured ceiling.
 *
 * This is what catches a regression like "someone statically imports
 * mermaid/katex/hljs from +layout.svelte (or from a module a layout
 * statically imports)": that import turns a dynamic-only dependency into a
 * static one, which is invisible to `npm run build`'s own chunk listing
 * (it just looks like one more chunk) but changes what THIS script's static
 * closure reaches for every route — exactly the class of bug #cee667d8 was
 * filed for (see markdown.ts / markdown-meta.ts split).
 *
 * Requires `npm run build` to have already produced .svelte-kit/output/.
 */

import { readFile } from 'node:fs/promises';
import { existsSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(__dirname, '..');
const clientDir = path.join(appRoot, '.svelte-kit/output/client');
const clientManifestPath = path.join(clientDir, '.vite/manifest.json');
const serverManifestPath = path.join(appRoot, '.svelte-kit/output/server/manifest-full.js');
const budgetPath = path.join(__dirname, 'budget.json');

const ENTRY_NODE_KEY = '.svelte-kit/generated/client-optimized/app.js';

function nodeManifestKey(i) {
	return `.svelte-kit/generated/client-optimized/nodes/${i}.js`;
}

/** BFS over `imports` only (never `dynamicImports`) — the set of chunks the browser fetches eagerly. */
function staticClosure(manifest, startKeys) {
	const seen = new Set();
	const queue = [...startKeys];
	while (queue.length > 0) {
		const key = queue.shift();
		if (seen.has(key)) continue;
		const entry = manifest[key];
		if (!entry) {
			throw new Error(`manifest is missing a key it referenced: ${key}`);
		}
		seen.add(key);
		for (const imp of entry.imports ?? []) {
			if (!seen.has(imp)) queue.push(imp);
		}
		// css counts too: it's fetched eagerly just like the JS is.
	}
	return seen;
}

function gzipSizeOfFile(relFile) {
	const abs = path.join(clientDir, relFile);
	if (!existsSync(abs)) {
		throw new Error(`chunk file referenced by manifest does not exist on disk: ${relFile}`);
	}
	return gzipSync(readFileSync(abs), { level: 9 }).length;
}

async function main() {
	if (!existsSync(clientManifestPath)) {
		console.error(`No client manifest at ${clientManifestPath} — run "npm run build" first.`);
		process.exit(2);
	}

	const manifest = JSON.parse(await readFile(clientManifestPath, 'utf8'));
	const { manifest: serverManifest } = await import(pathToFileURL(serverManifestPath).href);
	const routes = serverManifest._.routes;

	const budget = JSON.parse(await readFile(budgetPath, 'utf8'));

	let anyFailed = false;
	const results = [];

	for (const [name, spec] of Object.entries(budget)) {
		const route = routes.find((r) => r.id === spec.route);
		if (!route || !route.page) {
			throw new Error(`budget.json references route "${spec.route}" (surface "${name}") which doesn't exist or isn't a page in the built server manifest`);
		}
		const nodeIndices = [...route.page.layouts.filter((n) => n !== undefined), route.page.leaf];
		const startKeys = [ENTRY_NODE_KEY, ...nodeIndices.map(nodeManifestKey)];

		const closure = staticClosure(manifest, startKeys);

		let totalGzip = 0;
		const files = [];
		for (const key of closure) {
			const entry = manifest[key];
			const jsFiles = entry.file ? [entry.file] : [];
			const cssFiles = entry.css ?? [];
			for (const f of [...jsFiles, ...cssFiles]) {
				const size = gzipSizeOfFile(f);
				totalGzip += size;
				files.push({ file: f, gzip: size });
			}
		}

		const over = totalGzip > spec.max_gzip_bytes;
		if (over) anyFailed = true;

		results.push({ name, route: spec.route, totalGzip, ceiling: spec.max_gzip_bytes, over, files });
	}

	for (const r of results) {
		const status = r.over ? 'FAIL' : 'ok';
		console.log(`[${status}] ${r.name} (${r.route}): ${r.totalGzip} B gzip / ceiling ${r.ceiling} B (${r.files.length} files)`);
		if (r.over) {
			for (const f of r.files.sort((a, b) => b.gzip - a.gzip).slice(0, 10)) {
				console.log(`    ${f.gzip.toString().padStart(8)} B  ${f.file}`);
			}
		}
	}

	if (anyFailed) {
		console.error('\nBundle budget exceeded — see perf/budget.json. If this growth is real and reviewed, lower the ceiling only after confirming the growth is intentional; otherwise find and undo the static import that pulled a lazy-loaded dependency into the eager path.');
		process.exit(1);
	}

	console.log('\nBundle budget OK.');
}

main().catch((err) => {
	console.error(err);
	process.exit(2);
});
