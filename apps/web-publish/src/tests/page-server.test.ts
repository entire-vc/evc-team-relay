// @ts-nocheck — load() return type includes void (SvelteKit generic), tests use runtime values
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ---------------------------------------------------------------------------
// Auth-gate logic — load() behaviour
// ---------------------------------------------------------------------------
// Covers three share visibility paths:
//   - public: no auth required
//   - protected: password session via validateSession
//   - private: OAuth JWT via validateUserToken or agent_key from CP response
// ---------------------------------------------------------------------------

vi.mock('$lib/api', async (importOriginal) => {
	const mod = await importOriginal();
	return {
		...mod,
		getShareBySlug: vi.fn(),
		validateSession: vi.fn(),
		validateUserToken: vi.fn(),
		getFolderFileContent: vi.fn()
	};
});

// SvelteKit error() throws an object with a status property
vi.mock('@sveltejs/kit', async (importOriginal) => {
	const mod = await importOriginal();
	return {
		...mod,
		error: (status, message) => {
			const err = new Error(message);
			err.status = status;
			throw err;
		}
	};
});

import * as api from '$lib/api';
import { ShareNotFoundError } from '$lib/api';
import { load } from '../routes/[slug]/+page.server.js';

// Helper: build a minimal mock share
function makeShare(overrides = {}) {
	return {
		id: 'share-id',
		kind: 'document',
		path: 'Test Doc.md',
		visibility: 'public',
		web_slug: 'test-slug',
		web_noindex: false,
		created_at: new Date().toISOString(),
		updated_at: new Date().toISOString(),
		web_content: '# Hello',
		web_folder_items: null,
		web_doc_id: null,
		...overrides
	};
}

// Helper: build a minimal cookies mock
function makeCookies(values = {}) {
	return { get: (k) => values[k] };
}

// Helper: build a minimal url mock
function makeUrl(params = {}) {
	return { searchParams: { get: (k) => params[k] ?? null } };
}

// ---------------------------------------------------------------------------
// Public share
// ---------------------------------------------------------------------------

describe('load() — public share', () => {
	beforeEach(() => {
		vi.mocked(api.getShareBySlug).mockResolvedValue(makeShare({ visibility: 'public' }));
	});

	it('returns needsPassword=false without any session cookie', async () => {
		const data = await load({
			params: { slug: 'test-slug' },
			cookies: makeCookies(),
			url: makeUrl()
		});

		expect(data.needsPassword).toBe(false);
		expect(data.share.visibility).toBe('public');
	});

	it('does not call validateSession for public shares', async () => {
		await load({
			params: { slug: 'test-slug' },
			cookies: makeCookies(),
			url: makeUrl()
		});

		expect(api.validateSession).not.toHaveBeenCalled();
	});
});

// ---------------------------------------------------------------------------
// Protected share
// ---------------------------------------------------------------------------

describe('load() — protected share', () => {
	beforeEach(() => {
		vi.mocked(api.getShareBySlug).mockResolvedValue(makeShare({ visibility: 'protected' }));
	});

	it('returns needsPassword=true when no session cookie', async () => {
		vi.mocked(api.validateSession).mockResolvedValue({ valid: false, share_id: null });

		const data = await load({
			params: { slug: 'test-slug' },
			cookies: makeCookies(),
			url: makeUrl()
		});

		expect(data.needsPassword).toBe(true);
	});

	it('returns needsPassword=false when session is valid', async () => {
		vi.mocked(api.validateSession).mockResolvedValue({ valid: true, share_id: 'share-id' });

		const data = await load({
			params: { slug: 'test-slug' },
			cookies: makeCookies({ web_session: 'valid-token' }),
			url: makeUrl()
		});

		expect(data.needsPassword).toBe(false);
	});

	it('returns needsPassword=true when session is invalid', async () => {
		vi.mocked(api.validateSession).mockResolvedValue({ valid: false, share_id: null });

		const data = await load({
			params: { slug: 'test-slug' },
			cookies: makeCookies({ web_session: 'bad-token' }),
			url: makeUrl()
		});

		expect(data.needsPassword).toBe(true);
	});

	it('calls validateSession with the session cookie token', async () => {
		vi.mocked(api.validateSession).mockResolvedValue({ valid: true, share_id: 'share-id' });

		await load({
			params: { slug: 'test-slug' },
			cookies: makeCookies({ web_session: 'my-session-token' }),
			url: makeUrl()
		});

		expect(api.validateSession).toHaveBeenCalledWith('test-slug', 'my-session-token');
	});
});

// ---------------------------------------------------------------------------
// Private share
// ---------------------------------------------------------------------------

describe('load() — private share', () => {
	beforeEach(() => {
		vi.mocked(api.getShareBySlug).mockResolvedValue(
			makeShare({ visibility: 'private', web_content: null, web_folder_items: null })
		);
	});

	it('throws 401 when no auth token and no agent_key', async () => {
		vi.mocked(api.validateUserToken).mockResolvedValue({ valid: false });

		await expect(
			load({
				params: { slug: 'test-slug' },
				cookies: makeCookies(),
				url: makeUrl()
			})
		).rejects.toMatchObject({ status: 401 });
	});

	it('loads successfully when OAuth token is valid', async () => {
		vi.mocked(api.getShareBySlug).mockResolvedValue(
			makeShare({ visibility: 'private', web_content: '# Private Doc' })
		);
		vi.mocked(api.validateUserToken).mockResolvedValue({ valid: true, user_id: 'user-123' });

		const data = await load({
			params: { slug: 'test-slug' },
			cookies: makeCookies({ auth_token: 'valid-jwt' }),
			url: makeUrl()
		});

		expect(data.share.visibility).toBe('private');
	});

	it('loads successfully when agent_key is accepted by CP (content non-null)', async () => {
		vi.mocked(api.getShareBySlug).mockResolvedValue(
			makeShare({ visibility: 'private', web_content: '# Agent content' })
		);

		const data = await load({
			params: { slug: 'test-slug' },
			cookies: makeCookies(),
			url: makeUrl({ agent_key: 'sk-secret' })
		});

		expect(data.agentKey).toBe('sk-secret');
	});
});

// ---------------------------------------------------------------------------
// Server-rendered HTML body (TR-37)
// ---------------------------------------------------------------------------
// MarkdownViewer only ever rendered the body in onMount/$effect (browser-only),
// so a non-JS crawler/AI scraper got a body-less HTML shell. load() must render
// the markdown to HTML itself so the SSR output has real content.
// ---------------------------------------------------------------------------

describe('load() — server-rendered HTML (contentHtml/readmeHtml)', () => {
	it('returns contentHtml rendered from the document body', async () => {
		vi.mocked(api.getShareBySlug).mockResolvedValue(
			makeShare({ visibility: 'public', web_content: '# Hello\n\nWorld.' })
		);

		const data = await load({
			params: { slug: 'test-slug' },
			cookies: makeCookies(),
			url: makeUrl()
		});

		expect(data.contentHtml).toMatch(/<h1[^>]*>Hello<\/h1>/);
		expect(data.contentHtml).toContain('World.');
	});

	it('returns readmeHtml rendered from a folder README', async () => {
		vi.mocked(api.getShareBySlug).mockResolvedValue(
			makeShare({
				kind: 'folder',
				visibility: 'public',
				web_content: null,
				web_folder_items: [{ path: 'README.md', name: 'README.md', type: 'doc' }]
			})
		);
		vi.mocked(api.getFolderFileContent).mockResolvedValue({
			path: 'README.md',
			name: 'README.md',
			type: 'doc',
			content: '# Folder Overview'
		});

		const data = await load({
			params: { slug: 'test-slug' },
			cookies: makeCookies(),
			url: makeUrl()
		});

		expect(data.readmeHtml).toMatch(/<h1[^>]*>Folder Overview<\/h1>/);
	});

	it('readmeHtml is null when the folder has no README', async () => {
		vi.mocked(api.getShareBySlug).mockResolvedValue(
			makeShare({
				kind: 'folder',
				visibility: 'public',
				web_content: null,
				web_folder_items: [{ path: 'notes.md', name: 'notes.md', type: 'doc' }]
			})
		);

		const data = await load({
			params: { slug: 'test-slug' },
			cookies: makeCookies(),
			url: makeUrl()
		});

		expect(data.readmeHtml).toBeNull();
		expect(data.contentHtml).toBeNull();
	});
});

// ---------------------------------------------------------------------------
// 404 — unknown share
// ---------------------------------------------------------------------------

describe('load() — share not found', () => {
	it('throws 404 when getShareBySlug rejects with ShareNotFoundError', async () => {
		vi.mocked(api.getShareBySlug).mockRejectedValue(new ShareNotFoundError());

		await expect(
			load({
				params: { slug: 'does-not-exist' },
				cookies: makeCookies(),
				url: makeUrl()
			})
		).rejects.toMatchObject({ status: 404 });
	});
});

// ---------------------------------------------------------------------------
// 503 — upstream/network failure (TR-38: must NOT be mapped to 404)
// ---------------------------------------------------------------------------

describe('load() — upstream/network failure', () => {
	it('throws 503 when getShareBySlug rejects with a 5xx-style error', async () => {
		vi.mocked(api.getShareBySlug).mockRejectedValue(new Error('Failed to fetch share: Internal Server Error'));

		await expect(
			load({
				params: { slug: 'test-slug' },
				cookies: makeCookies(),
				url: makeUrl()
			})
		).rejects.toMatchObject({ status: 503 });
	});

	it('throws 503 when getShareBySlug rejects with a network/timeout error', async () => {
		vi.mocked(api.getShareBySlug).mockRejectedValue(new DOMException('The operation was aborted', 'AbortError'));

		await expect(
			load({
				params: { slug: 'test-slug' },
				cookies: makeCookies(),
				url: makeUrl()
			})
		).rejects.toMatchObject({ status: 503 });
	});
});
