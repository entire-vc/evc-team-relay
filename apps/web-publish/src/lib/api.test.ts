import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
	fetchWithTimeout,
	getShareBySlug,
	authenticateShare,
	validateSession,
	getSitemapXml,
	ShareNotFoundError
} from './api.js';

// ---------------------------------------------------------------------------
// fetchWithTimeout — timeout behaviour
// ---------------------------------------------------------------------------

describe('fetchWithTimeout', () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('aborts the underlying fetch via AbortController on timeout', async () => {
		// Verify the AbortSignal is fired rather than testing promise rejection
		// (rejection testing causes unhandled-rejection warnings due to microtask ordering)
		let capturedSignal: AbortSignal | undefined;
		vi.stubGlobal(
			'fetch',
			vi.fn((_url: string, init: RequestInit) => {
				capturedSignal = init?.signal as AbortSignal | undefined;
				// Return a promise that never resolves; catch prevents unhandled-rejection
				const p = new Promise<Response>(() => {});
				p.catch(() => {});
				return p;
			})
		);

		const promise = fetchWithTimeout('http://example.com', {}, 100);
		promise.catch(() => {});

		await vi.advanceTimersByTimeAsync(200);

		// The signal must have been aborted
		expect(capturedSignal?.aborted).toBe(true);
		vi.unstubAllGlobals();
	});

	it('resolves normally when fetch completes before timeout', async () => {
		const mockResponse = new Response(JSON.stringify({ ok: true }), { status: 200 });
		vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(mockResponse)));

		const result = await fetchWithTimeout('http://example.com', {}, 5000);
		expect(result.status).toBe(200);
		vi.unstubAllGlobals();
	});

	it('passes through init headers', async () => {
		let capturedInit: RequestInit | undefined;
		vi.stubGlobal(
			'fetch',
			vi.fn((_, init: RequestInit) => {
				capturedInit = init;
				return Promise.resolve(new Response('{}', { status: 200 }));
			})
		);

		await fetchWithTimeout('http://example.com', { headers: { 'X-Test': 'yes' } }, 5000);
		expect((capturedInit?.headers as Record<string, string>)?.['X-Test']).toBe('yes');
		vi.unstubAllGlobals();
	});
});

// ---------------------------------------------------------------------------
// getShareBySlug — error handling
// ---------------------------------------------------------------------------

describe('getShareBySlug', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('throws ShareNotFoundError on 404', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(() => Promise.resolve(new Response('not found', { status: 404 })))
		);

		await expect(getShareBySlug('missing-slug')).rejects.toThrow('Share not found or not published');
		await expect(getShareBySlug('missing-slug')).rejects.toBeInstanceOf(ShareNotFoundError);
	});

	it('throws a plain (non-ShareNotFoundError) error on other non-ok status', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(() => Promise.resolve(new Response('error', { status: 500, statusText: 'Internal Server Error' })))
		);

		await expect(getShareBySlug('some-slug')).rejects.toThrow('Failed to fetch share');
		await expect(getShareBySlug('some-slug')).rejects.not.toBeInstanceOf(ShareNotFoundError);
	});

	it('returns parsed share on 200', async () => {
		const share = { id: 'abc', web_slug: 'my-share', visibility: 'public' };
		vi.stubGlobal(
			'fetch',
			vi.fn(() => Promise.resolve(new Response(JSON.stringify(share), { status: 200 })))
		);

		const result = await getShareBySlug('my-share');
		expect(result.web_slug).toBe('my-share');
	});

	it('appends agent_key query param when provided', async () => {
		let capturedUrl = '';
		vi.stubGlobal(
			'fetch',
			vi.fn((url: string) => {
				capturedUrl = url;
				return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }));
			})
		);

		await getShareBySlug('my-share', 'secret-key').catch(() => {});
		expect(capturedUrl).toContain('agent_key=secret-key');
	});
});

// ---------------------------------------------------------------------------
// authenticateShare — error handling
// ---------------------------------------------------------------------------

describe('authenticateShare', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('throws "Invalid password" on 401', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(() => Promise.resolve(new Response('unauthorized', { status: 401 })))
		);

		await expect(authenticateShare('slug', 'wrong')).rejects.toThrow('Invalid password');
	});

	it('returns auth response on 200', async () => {
		const payload = { message: 'ok', share_id: 'abc123' };
		vi.stubGlobal(
			'fetch',
			vi.fn(() => Promise.resolve(new Response(JSON.stringify(payload), { status: 200 })))
		);

		const result = await authenticateShare('slug', 'correct');
		expect(result.share_id).toBe('abc123');
	});
});

// ---------------------------------------------------------------------------
// validateSession — graceful failure
// ---------------------------------------------------------------------------

describe('validateSession', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('returns { valid: false } on non-ok response', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(() => Promise.resolve(new Response('forbidden', { status: 403 })))
		);

		const result = await validateSession('slug', 'bad-token');
		expect(result.valid).toBe(false);
		expect(result.share_id).toBeNull();
	});

	it('returns parsed session data on 200', async () => {
		const payload = { valid: true, share_id: 'share-xyz' };
		vi.stubGlobal(
			'fetch',
			vi.fn(() => Promise.resolve(new Response(JSON.stringify(payload), { status: 200 })))
		);

		const result = await validateSession('slug', 'good-token');
		expect(result.valid).toBe(true);
		expect(result.share_id).toBe('share-xyz');
	});
});

// ---------------------------------------------------------------------------
// getSitemapXml — proxy to Control Plane
// ---------------------------------------------------------------------------

describe('getSitemapXml', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('throws on non-ok response', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(() => Promise.resolve(new Response('error', { status: 500, statusText: 'Internal Server Error' })))
		);

		await expect(getSitemapXml()).rejects.toThrow('Failed to fetch sitemap.xml');
	});

	it('returns the XML body on 200', async () => {
		const xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>\n';
		vi.stubGlobal(
			'fetch',
			vi.fn(() => Promise.resolve(new Response(xml, { status: 200 })))
		);

		const result = await getSitemapXml();
		expect(result).toBe(xml);
	});
});
