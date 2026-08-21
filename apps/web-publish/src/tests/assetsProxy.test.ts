import { describe, it, expect, vi, afterEach } from 'vitest';
import { GET } from '../routes/[slug]/_assets/[...path]/+server.js';

// TR-60: the proxy used to hardcode `Cache-Control: public, max-age=86400`
// on every asset response regardless of the share's actual visibility —
// control-plane already decides the right policy (public shares get
// `public, max-age=...`; private/protected shares get none at all). These
// tests pin that the proxy now forwards whatever control-plane decided
// instead of overriding it.

function makeEvent(cacheControlFromCp: string | null, overrides: Record<string, string> = {}) {
	const upstreamHeaders = new Headers({ 'content-type': 'image/png' });
	if (cacheControlFromCp !== null) {
		upstreamHeaders.set('cache-control', cacheControlFromCp);
	}

	vi.stubGlobal(
		'fetch',
		vi.fn(() =>
			Promise.resolve(
				new Response(new ArrayBuffer(4), {
					status: 200,
					headers: upstreamHeaders
				})
			)
		)
	);

	return {
		params: { slug: 'test-slug', path: 'image.png' },
		request: new Request('http://localhost/test-slug/_assets/image.png', {
			headers: overrides
		}),
		url: new URL('http://localhost/test-slug/_assets/image.png')
	};
}

describe('GET /[slug]/_assets/[...path] — Cache-Control forwarding (TR-60)', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it("forwards control-plane's public Cache-Control unchanged (public share, no regression)", async () => {
		const event = makeEvent('public, max-age=86400');
		const response = await GET(event as never);

		expect(response.headers.get('Cache-Control')).toBe('public, max-age=86400');
	});

	it('does NOT set Cache-Control at all when control-plane sets none (private/protected share)', async () => {
		const event = makeEvent(null);
		const response = await GET(event as never);

		expect(response.headers.has('Cache-Control')).toBe(false);
	});

	it('never falls back to a hardcoded public directive when upstream omits one', async () => {
		const event = makeEvent(null);
		const response = await GET(event as never);

		const cacheControl = response.headers.get('Cache-Control');
		expect(cacheControl === null || !cacheControl.includes('public')).toBe(true);
	});

	it('forwards a private/no-store directive verbatim if control-plane ever sends one', async () => {
		const event = makeEvent('private, no-store');
		const response = await GET(event as never);

		expect(response.headers.get('Cache-Control')).toBe('private, no-store');
	});

	it('still sets Content-Type from the upstream response', async () => {
		const event = makeEvent('public, max-age=86400');
		const response = await GET(event as never);

		expect(response.headers.get('Content-Type')).toBe('image/png');
	});
});

// #d0e32ac0: the proxy used to collapse every non-2xx upstream response
// (401 private share, 403 wrong scope, 404 missing file, 500 storage error)
// into an identical `404 "Asset not found"`, making them indistinguishable
// on the public surface. These tests pin the real status-code mapping and,
// separately, that the upstream response BODY is never forwarded on an
// error branch — control-plane's 500 carries a raw S3Error with the bucket
// name and object key, which must never reach a public caller.

function makeErrorEvent(
	upstream: { status: number; body?: string } | 'throw'
): { params: { slug: string; path: string }; request: Request; url: URL } {
	if (upstream === 'throw') {
		vi.stubGlobal(
			'fetch',
			vi.fn(() => Promise.reject(new Error('network error')))
		);
	} else {
		vi.stubGlobal(
			'fetch',
			vi.fn(() =>
				Promise.resolve(
					new Response(upstream.body ?? '', { status: upstream.status })
				)
			)
		);
	}

	return {
		params: { slug: 'test-slug', path: 'image.png' },
		request: new Request('http://localhost/test-slug/_assets/image.png'),
		url: new URL('http://localhost/test-slug/_assets/image.png')
	};
}

describe('GET /[slug]/_assets/[...path] — upstream error status mapping (#d0e32ac0)', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('maps upstream 401 (private share, no creds) to 401', async () => {
		const response = await GET(
			makeErrorEvent({
				status: 401,
				body: JSON.stringify({ error: { code: 401, message: 'Authentication required for private share' } })
			}) as never
		);
		expect(response.status).toBe(401);
	});

	it('does not set WWW-Authenticate on 401 (would trigger a native basic-auth dialog on <img> loads)', async () => {
		const response = await GET(makeErrorEvent({ status: 401 }) as never);
		expect(response.headers.has('WWW-Authenticate')).toBe(false);
	});

	it('maps upstream 403 (wrong scope) to 403', async () => {
		const response = await GET(makeErrorEvent({ status: 403 }) as never);
		expect(response.status).toBe(403);
	});

	it('maps upstream 404 (file genuinely missing) to 404 "Asset not found" — unchanged', async () => {
		const response = await GET(makeErrorEvent({ status: 404 }) as never);
		expect(response.status).toBe(404);
		expect(await response.text()).toBe('Asset not found');
	});

	it('maps upstream 500 to 502 (proxy is fine, upstream failed — not our own outage)', async () => {
		const response = await GET(
			makeErrorEvent({
				status: 500,
				body: JSON.stringify({
					detail: "Failed to retrieve asset: NoSuchKey: The specified key does not exist. Bucket: web-assets, Key: web-assets/share-123/secret.png"
				})
			}) as never
		);
		expect(response.status).toBe(502);
	});

	it('maps a fetch throw/network error to 504 (upstream never answered, distinct from a 5xx)', async () => {
		const response = await GET(makeErrorEvent('throw') as never);
		expect(response.status).toBe(504);
	});

	it('never forwards the upstream error body — no bucket name, key, or S3Error leak', async () => {
		const response = await GET(
			makeErrorEvent({
				status: 500,
				body: JSON.stringify({
					detail: "Failed to retrieve asset: NoSuchKey: The specified key does not exist. Bucket: web-assets, Key: web-assets/share-123/secret.png"
				})
			}) as never
		);
		const text = await response.text();
		expect(text).not.toContain('web-assets/');
		expect(text).not.toContain('Bucket');
		expect(text).not.toContain('S3Error');
		expect(text).not.toContain('NoSuchKey');
	});

	it('never forwards the upstream error body on 401/403 either', async () => {
		const response = await GET(
			makeErrorEvent({
				status: 401,
				body: JSON.stringify({ error: { code: 401, message: 'Authentication required for private share', internal_share_id: 'share-123' } })
			}) as never
		);
		const text = await response.text();
		expect(text).not.toContain('share-123');
		expect(text).not.toContain('internal_share_id');
	});
});
