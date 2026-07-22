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
