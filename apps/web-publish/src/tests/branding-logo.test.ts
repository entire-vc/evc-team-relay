// Same-origin logo delivery (#cee667d8): the logo is the LCP element on the
// home page, and rendered cross-origin it waits on DNS+TCP+TLS first.

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import {
	isControlPlanePath,
	controlPlaneAssetPath,
	proxyableLogoPath,
	LOGO_PROXY_PATH
} from '../lib/branding.js';

describe('isControlPlanePath', () => {
	it('accepts a control-plane-relative path', () => {
		expect(isControlPlanePath('/static/img/evc-ava.png')).toBe(true);
	});

	it.each([
		'https://cdn.example.com/logo.png',
		'//evil.example.com/logo.png',
		'/static/../../etc/passwd',
		'/static\\..\\x',
		'logo.png',
		''
	])('rejects %s', (value) => {
		expect(isControlPlanePath(value)).toBe(false);
	});
});

describe('controlPlaneAssetPath', () => {
	const PUBLIC = 'https://cp.tr.entire.vc';

	it('passes a relative path through', () => {
		expect(controlPlaneAssetPath('/static/img/a.png', PUBLIC)).toBe('/static/img/a.png');
	});

	it('extracts the path from an absolute URL on the control-plane origin (what /server/info returns)', () => {
		expect(controlPlaneAssetPath('https://cp.tr.entire.vc/static/img/evc-ava.png', PUBLIC)).toBe(
			'/static/img/evc-ava.png'
		);
	});

	it.each([
		'https://cdn.example.com/static/img/a.png',
		'https://cp.tr.entire.vc.evil.example.com/a.png',
		'http://cp.tr.entire.vc/a.png',
		'not a url'
	])('rejects %s', (value) => {
		expect(controlPlaneAssetPath(value, PUBLIC)).toBeNull();
	});

	it('a traversal in an absolute URL is already normalised to a plain path on the same origin', () => {
		expect(controlPlaneAssetPath('https://cp.tr.entire.vc/static/../x.png', PUBLIC)).toBe('/x.png');
	});

	it('refuses absolute URLs when no public control-plane origin is configured', () => {
		expect(controlPlaneAssetPath('https://cp.tr.entire.vc/a.png', '')).toBeNull();
	});
});

describe('proxyableLogoPath', () => {
	const PUBLIC = 'https://cp.tr.entire.vc';

	it('proxies raster logos', () => {
		expect(proxyableLogoPath('https://cp.tr.entire.vc/static/img/a.png', PUBLIC)).toBe(
			'/static/img/a.png'
		);
	});

	// An SVG opened as a document runs script on OUR origin (CSP allows
	// 'unsafe-inline'), the one holding share session cookies.
	it.each(['/static/img/a.svg', '/static/img/A.SVG', '/static/img/a.svgz', '/static/a.svg?v=2'])(
		'never proxies %s',
		(path) => {
			expect(proxyableLogoPath(path, PUBLIC)).toBeNull();
		}
	);
});

function serverInfo(logo_url: string) {
	return new Response(JSON.stringify({ branding: { logo_url } }), {
		headers: { 'content-type': 'application/json' }
	});
}

describe(`GET ${LOGO_PROXY_PATH}`, () => {
	beforeEach(() => {
		// The handler keeps a small in-process copy; start each case cold.
		vi.resetModules();
	});
	afterEach(() => {
		vi.unstubAllGlobals();
		vi.unstubAllEnvs();
	});

	async function callGet() {
		vi.stubEnv('PUBLIC_CONTROL_PLANE_URL', 'https://cp.tr.entire.vc');
		const { GET } = await import('../routes/_branding/logo/+server.js');
		return GET({} as never);
	}

	it('serves the control-plane logo from our origin with a cache policy', async () => {
		const fetchMock = vi.fn((url: string) =>
			Promise.resolve(
				url.endsWith('/server/info')
					? serverInfo('/static/img/evc-ava.png')
					: new Response(new Uint8Array([1, 2, 3, 4]), { headers: { 'content-type': 'image/png' } })
			)
		);
		vi.stubGlobal('fetch', fetchMock);

		const res = await callGet();

		expect(res.status).toBe(200);
		expect(res.headers.get('content-type')).toBe('image/png');
		expect(res.headers.get('cache-control')).toMatch(/public, max-age=\d+/);
		expect(new Uint8Array(await res.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3, 4]));
		expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/static/img/evc-ava.png'))).toBe(
			true
		);
	});

	it('proxies the absolute control-plane URL that /server/info really returns', async () => {
		const fetchMock = vi.fn((url: string) =>
			Promise.resolve(
				url.endsWith('/server/info')
					? serverInfo('https://cp.tr.entire.vc/static/img/evc-ava.png')
					: new Response(new Uint8Array([9]), { headers: { 'content-type': 'image/png' } })
			)
		);
		vi.stubGlobal('fetch', fetchMock);

		const res = await callGet();

		expect(res.status).toBe(200);
		// fetched from the internal control-plane URL, by path — not the public host
		expect(fetchMock.mock.calls.at(-1)![0]).toMatch(/\/static\/img\/evc-ava\.png$/);
		expect(String(fetchMock.mock.calls.at(-1)![0])).not.toContain('cp.tr.entire.vc');
	});

	it('does not proxy an absolute logo URL on another host (would be an open fetch of any host)', async () => {
		const fetchMock = vi.fn(() => Promise.resolve(serverInfo('https://evil.example.com/x.png')));
		vi.stubGlobal('fetch', fetchMock);

		const res = await callGet();

		expect(res.status).toBe(404);
		expect(fetchMock).toHaveBeenCalledTimes(1); // server info only, never the logo host
	});

	it('refuses to reflect a non-image upstream body', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn((url: string) =>
				Promise.resolve(
					url.endsWith('/server/info')
						? serverInfo('/static/img/evc-ava.png')
						: new Response('<script>alert(1)</script>', { headers: { 'content-type': 'text/html' } })
				)
			)
		);

		const res = await callGet();

		expect(res.status).toBe(502);
	});

	it('answers 504 when the control plane does not answer', async () => {
		vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('timeout'))));

		const res = await callGet();

		expect(res.status).toBe(504);
	});

	function pngFetch(extra: (url: string) => Response | null = () => null) {
		return vi.fn((url: string, _init?: RequestInit) =>
			Promise.resolve(
				extra(url) ??
					(url.endsWith('/server/info')
						? serverInfo('/static/img/evc-ava.png')
						: new Response(new Uint8Array([1, 2]), { headers: { 'content-type': 'image/png' } }))
			)
		);
	}

	it('sends a sandboxed, script-less CSP so the URL is inert even when opened directly', async () => {
		vi.stubGlobal('fetch', pngFetch());
		const res = await callGet();
		expect(res.headers.get('content-security-policy')).toBe("default-src 'none'; sandbox");
	});

	it('refuses SVG from upstream even with an image/* content type', async () => {
		vi.stubGlobal(
			'fetch',
			pngFetch((url) =>
				url.endsWith('/server/info')
					? null
					: new Response('<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>', {
							headers: { 'content-type': 'image/svg+xml' }
						})
			)
		);
		expect((await callGet()).status).toBe(502);
	});

	it('does not follow upstream redirects off the control-plane origin', async () => {
		const fetchMock = pngFetch((url) =>
			url.endsWith('/server/info')
				? null
				: new Response(null, { status: 302, headers: { location: 'https://evil.example.com/x.png' } })
		);
		vi.stubGlobal('fetch', fetchMock);

		expect((await callGet()).status).toBe(502);
		const logoCall = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/static/img/evc-ava.png'));
		expect(logoCall![1]).toMatchObject({ redirect: 'manual' });
	});

	it('rejects an oversized logo from its declared length, before buffering it', async () => {
		vi.stubGlobal(
			'fetch',
			pngFetch((url) =>
				url.endsWith('/server/info')
					? null
					: new Response(new Uint8Array([1]), {
							headers: { 'content-type': 'image/png', 'content-length': String(5 * 1024 * 1024) }
						})
			)
		);
		expect((await callGet()).status).toBe(502);
	});

	it('serves a repeat request from the in-process copy without touching the control plane', async () => {
		const fetchMock = pngFetch();
		vi.stubGlobal('fetch', fetchMock);
		vi.stubEnv('PUBLIC_CONTROL_PLANE_URL', 'https://cp.tr.entire.vc');
		const { GET } = await import('../routes/_branding/logo/+server.js');

		expect((await GET({} as never)).status).toBe(200);
		const callsAfterFirst = fetchMock.mock.calls.length;
		expect((await GET({} as never)).status).toBe(200);

		expect(fetchMock.mock.calls.length).toBe(callsAfterFirst);
	});
});
