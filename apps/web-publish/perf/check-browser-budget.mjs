import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { gzipSync } from 'node:zlib';
import { chromium } from 'playwright';

// Exercise the production adapter with a local control-plane fixture. No
// test routes, credentials, production data or browser profile are needed.
const appRoot = new URL('../', import.meta.url);
const budget = JSON.parse(await readFile(new URL('budget.json', import.meta.url)));
const content = '# Performance fixture\n\nPublished document content is visible before hydration.\n\n' +
	'## Reading\n\nA repeatable paragraph with a [local link](/perf-fixture/Notes/guide.md).\n\n'.repeat(12);
const richContent = '# Renderer fixture\n\n```javascript\nconst answer = 42;\n```\n\n$$x^2 + y^2 = z^2$$\n\n```mermaid\ngraph TD; A-->B;\n```';
const items = [{ path: 'README.md', name: 'README.md', type: 'doc' },
	...Array.from({ length: 30 }, (_, i) => ({ path: `Notes/note-${i}.md`, name: `note-${i}.md`, type: 'doc' })),
	{ path: 'Notes/guide.md', name: 'guide.md', type: 'doc' }];
const logo = await readFile(new URL('../control-plane/app/static/img/evc-ava.png', appRoot));
const unexpectedRequests = [];
const upstream = createServer((req, res) => {
	const url = new URL(req.url, 'http://fixture');
	res.setHeader('Content-Type', 'application/json');
	if (url.pathname === '/server/info') {
		res.end(JSON.stringify({ id: 'fixture', name: 'Fixture', version: '1', features: {}, branding: {
			name: 'Performance fixture', logo_url: '/static/img/evc-ava.png', favicon_url: '',
			custom_head_code: '', custom_body_code: ''
		} }));
	} else if (url.pathname === '/static/img/evc-ava.png') {
		res.setHeader('Content-Type', 'image/png');
		res.end(logo);
	} else if (url.pathname === '/v1/web/shares/perf-fixture/files') {
		res.end(JSON.stringify({ path: url.searchParams.get('path'), content }));
	} else if (['/v1/web/shares/perf-fixture', '/v1/web/shares/renderer-fixture'].includes(url.pathname)) {
		const rich = url.pathname.endsWith('/renderer-fixture');
		res.end(JSON.stringify({ id: 'fixture', kind: rich ? 'doc' : 'folder', path: 'Fixture', visibility: 'public',
			web_slug: rich ? 'renderer-fixture' : 'perf-fixture', web_noindex: true,
			created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
			web_content: rich ? richContent : null, web_folder_items: rich ? null : items }));
	} else {
		unexpectedRequests.push(url.pathname);
		res.writeHead(404).end('{}');
	}
});
await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
const upstreamUrl = `http://127.0.0.1:${upstream.address().port}`;
const reservation = createServer();
await new Promise(resolve => reservation.listen(0, '127.0.0.1', resolve));
const port = reservation.address().port;
await new Promise(resolve => reservation.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const server = spawn(process.execPath, ['build/index.js'], {
	cwd: appRoot,
	env: { ...process.env, HOST: '127.0.0.1', PORT: String(port), ORIGIN: origin,
		CONTROL_PLANE_URL: upstreamUrl, PUBLIC_CONTROL_PLANE_URL: upstreamUrl,
		PUBLIC_LAZY_RENDERERS_DISABLED: 'false' },
	stdio: ['ignore', 'pipe', 'pipe']
});
let serverLog = '';
server.stdout.on('data', chunk => { serverLog += chunk; });
server.stderr.on('data', chunk => { serverLog += chunk; });
let serverError;
server.on('error', error => { serverError = error; });
let browser;
const report = { cpu: 4, viewport: { width: 1440, height: 900 }, cold_cache: true, surfaces: [] };
const p75 = values => [...values].sort((a, b) => a - b)[Math.ceil(values.length * 0.75) - 1];

// Observers start before any page script. TBT counts only long-task overlap
// after FCP, in the fixed navigation-to-2s-after-load measurement window.
function observe() {
	window.__perf = { lcp: 0, tasks: [] };
	new PerformanceObserver(list => {
		for (const entry of list.getEntries()) window.__perf.lcp = entry.startTime;
	}).observe({ type: 'largest-contentful-paint', buffered: true });
	new PerformanceObserver(list => {
		for (const entry of list.getEntries()) window.__perf.tasks.push({ start: entry.startTime, duration: entry.duration });
	}).observe({ type: 'longtask', buffered: true });
}

async function measure(spec) {
	// Fresh processes also reset engine/font caches and bound browser memory.
	const sampleBrowser = await chromium.launch({ headless: true });
	let context;
	try {
		context = await sampleBrowser.newContext({ viewport: report.viewport, serviceWorkers: 'block' });
		const page = await context.newPage();
		const cdp = await context.newCDPSession(page);
		await cdp.send('Network.enable');
		await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
		await cdp.send('Emulation.setCPUThrottlingRate', { rate: spec.cpu_rate });
		await context.addInitScript(observe);
		const errors = [];
		const failedRequests = [];
		const scripts = new Map();
		const pendingBodies = [];
		page.on('pageerror', error => errors.push(error.message));
		page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
		page.on('requestfailed', request => failedRequests.push(request.url()));
		page.on('response', response => {
			if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
			if (response.request().resourceType() === 'script') {
				// Normalize actual requested bodies to gzip: adapter-node on localhost
				// has no Caddy compression. Includes dynamic imports, unlike the static
				// manifest budget; this is not a claim about local wire bytes.
				pendingBodies.push(response.body()
					.then(body => scripts.set(response.url(), gzipSync(body, { level: 9 }).length))
					.catch(error => errors.push(`Cannot measure ${response.url()}: ${error.message}`)));
			}
		});
		const response = await page.goto(origin + spec.path, { waitUntil: 'load', timeout: 30000 });
		assert.equal(response.status(), 200);
		await page.locator(spec.path === '/' ? 'h1.instance-name' : '.markdown-content h1').waitFor();
		assert.match(await page.locator('main').innerText(), /Performance fixture/);
		await page.waitForTimeout(2000);
		await Promise.all(pendingBodies);
		assert.deepEqual(errors, [], 'browser console/runtime/HTTP errors');
		assert.deepEqual(failedRequests, [], 'failed requests');
		assert.ok(scripts.size > 0, 'hydrated page must load client scripts');
		const metrics = await page.evaluate(() => {
			const fcp = performance.getEntriesByName('first-contentful-paint')[0]?.startTime;
			if (fcp === undefined || window.__perf.lcp <= 0) throw new Error('Missing paint observations');
			const tasks = window.__perf.tasks.filter(task => task.start + task.duration > fcp);
			return { lcp_ms: window.__perf.lcp, long_tasks: tasks.length,
				tbt_ms: tasks.reduce((sum, task) => sum + Math.max(0, task.duration - Math.max(0, fcp - task.start) - 50), 0) };
		});
		// Check hydration after sampling so interaction does not contaminate LCP.
		if (spec.path !== '/') {
			await page.getByRole('button', { name: 'Collapse sidebar', exact: true }).click();
			await page.waitForFunction(() => localStorage.getItem('sidebar-collapsed') === 'true');
		}
		return { ...metrics, js_gzip_bytes: [...scripts.values()].reduce((a, b) => a + b, 0), scripts: [...scripts.keys()] };
	} finally {
		await context?.close();
		await sampleBrowser.close();
	}
}

try {
	let ready = false;
	for (let attempt = 0; attempt < 100; attempt++) {
		if (serverError || server.exitCode !== null) throw new Error(`Production server failed: ${serverError ?? serverLog}`);
		try {
			if ((await fetch(origin, { signal: AbortSignal.timeout(1000) })).ok) { ready = true; break; }
		} catch { /* wait for adapter startup */ }
		await new Promise(resolve => setTimeout(resolve, 100));
	}
	assert.ok(ready, `Production server did not become ready: ${serverLog}`);
	let failed = false;
	for (const [name, entry] of Object.entries(budget)) {
		const spec = entry.browser;
		assert.ok(spec && spec.runs >= 10 && spec.cpu_rate === 4, `Invalid browser budget for ${name}`);
		for (const key of ['max_js_gzip_bytes', 'max_long_tasks', 'warn_tbt_ms', 'warn_lcp_ms', 'timing_tolerance']) {
			assert.ok(Number.isFinite(spec[key]) && spec[key] >= 0, `Invalid ${key} for ${name}`);
		}
		assert.ok(spec.timing_tolerance >= 0.15, 'Timing warning tolerance must be at least 15%');
		const samples = [];
		for (let run = 0; run < spec.runs; run++) samples.push(await measure(spec));
		const metrics = Object.fromEntries(['js_gzip_bytes', 'long_tasks', 'tbt_ms', 'lcp_ms'].map(key => [key, p75(samples.map(sample => sample[key]))]));
		const violations = [];
		if (metrics.js_gzip_bytes > spec.max_js_gzip_bytes) violations.push('js_gzip_bytes');
		if (metrics.long_tasks > spec.max_long_tasks) violations.push('long_tasks');
		const warnings = ['tbt', 'lcp'].filter(key => metrics[`${key}_ms`] > spec[`warn_${key}_ms`] * (1 + spec.timing_tolerance));
		failed ||= violations.length > 0;
		report.surfaces.push({ name, path: spec.path, budget: spec, p75: metrics, violations, warnings, samples });
		console.log(`[${violations.length ? 'FAIL' : 'ok'}] ${name}: ${JSON.stringify(metrics)}${warnings.length ? ` timing warnings: ${warnings}` : ''}`);
	}
	// Positive renderer control: the large dynamic graph must work when needed.
	browser = await chromium.launch({ headless: true });
	const context = await browser.newContext({ serviceWorkers: 'block' });
	try {
		const page = await context.newPage();
		const errors = [];
		page.on('pageerror', error => errors.push(error.message));
		await page.goto(origin + '/renderer-fixture');
		await page.locator('.mermaid-rendered svg').waitFor({ timeout: 30000 });
		assert.ok(await page.locator('pre code .hljs-keyword').count(), 'highlighted code');
		assert.ok(await page.locator('.katex').count(), 'rendered formula');
		assert.deepEqual(errors, []);
		report.renderer_smoke = 'PASS';
	} finally {
		await context.close();
	}
	assert.deepEqual(unexpectedRequests, [], 'unhandled fixture requests');
	if (failed) process.exitCode = 1;
} catch (error) {
	report.error = String(error.stack ?? error);
	console.error(error);
	process.exitCode = 1;
} finally {
	await writeFile(new URL('browser-results.json', import.meta.url), JSON.stringify(report, null, 2) + '\n');
	await browser?.close();
	if (server.exitCode === null && !serverError) {
		const exited = once(server, 'exit');
		server.kill('SIGTERM');
		const timer = setTimeout(() => server.kill('SIGKILL'), 5000);
		await exited;
		clearTimeout(timer);
	}
	await new Promise(resolve => upstream.close(resolve));
}
