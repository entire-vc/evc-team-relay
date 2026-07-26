// @ts-nocheck — load() return type includes void (SvelteKit generic), tests use runtime values
import { describe, it, expect, vi } from 'vitest';

// ---------------------------------------------------------------------------
// [slug]/[...path] load() — server-rendered HTML body (TR-37)
// ---------------------------------------------------------------------------
// Same fix as the doc route: the folder-file viewer must render the markdown
// to HTML in load() so non-JS crawlers/AI scrapers see real content, not just
// the client-only MarkdownViewer skeleton.
// ---------------------------------------------------------------------------

vi.mock('$env/dynamic/private', () => ({ env: {} }));

vi.mock('$lib/api', () => ({
	getShareBySlug: vi.fn(),
	validateSession: vi.fn(),
	validateUserToken: vi.fn(),
	getFolderFileContent: vi.fn()
}));

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
import { load } from '../routes/[slug]/[...path]/+page.server.js';

function makeShare(overrides = {}) {
	return {
		id: 'share-id',
		kind: 'folder',
		path: 'Test Folder',
		visibility: 'public',
		web_slug: 'test-slug',
		web_noindex: false,
		created_at: new Date().toISOString(),
		updated_at: new Date().toISOString(),
		web_content: null,
		web_folder_items: [{ path: 'Notes/Doc.md', name: 'Doc.md', type: 'doc' }],
		web_doc_id: null,
		...overrides
	};
}

function makeCookies(values = {}) {
	return { get: (k) => values[k] };
}

function makeUrl(params = {}) {
	return { searchParams: { get: (k) => params[k] ?? null } };
}

describe('folder-file load() — server-rendered HTML (contentHtml)', () => {
	it('returns contentHtml rendered from the file body', async () => {
		vi.mocked(api.getShareBySlug).mockResolvedValue(makeShare());
		vi.mocked(api.getFolderFileContent).mockResolvedValue({
			path: 'Notes/Doc.md',
			name: 'Doc.md',
			type: 'doc',
			content: '# File Title\n\nBody text.'
		});

		const data = await load({
			params: { slug: 'test-slug', path: 'Notes/Doc.md' },
			cookies: makeCookies(),
			url: makeUrl()
		});

		expect(data.contentHtml).toMatch(/<h1[^>]*>File Title<\/h1>/);
		expect(data.contentHtml).toContain('Body text.');
	});

	it('renders the not-yet-synced placeholder when the file fetch fails', async () => {
		vi.mocked(api.getShareBySlug).mockResolvedValue(makeShare());
		vi.mocked(api.getFolderFileContent).mockRejectedValue(new Error('boom'));

		const data = await load({
			params: { slug: 'test-slug', path: 'Notes/Doc.md' },
			cookies: makeCookies(),
			url: makeUrl()
		});

		expect(data.contentHtml).toContain('not yet synced');
	});
});
