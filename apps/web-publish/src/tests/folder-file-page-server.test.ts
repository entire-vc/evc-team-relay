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

// ---------------------------------------------------------------------------
// [slug]/[...path] load() — matching a punctuation-bearing filename (#546ce7e3)
// ---------------------------------------------------------------------------
// A link built by FileTree.svelte for an item goes through slugifyPath,
// which now percent-encodes each segment (e.g. "50% done.md" -> "50%25-done.md")
// so the href actually works. By the time SvelteKit's [...path] rest param
// reaches load(), it has already been percent-DECODED once by the router
// (decode_pathname + decode_params) — so `params.path` here arrives as
// "50%-done.md", not the encoded form. Matching must compare against the
// plain hyphenated form (hyphenatePath), not the encoded one (slugifyPath) —
// this is exactly the regression the old `slugifyPath(item.path) === path`
// comparison would reintroduce the moment any encoded character appears.
// ---------------------------------------------------------------------------

describe('folder-file load() — matches items with URL-meaningful characters', () => {
	it('matches an item via the exact-path branch when the name has parens (conflict-copy shape)', async () => {
		const conflictCopyName = 'note (relay conflict 2026-09-05T20-25-23-268Z).md';
		vi.mocked(api.getShareBySlug).mockResolvedValue(
			makeShare({
				web_folder_items: [{ path: conflictCopyName, name: conflictCopyName, type: 'doc' }]
			})
		);
		vi.mocked(api.getFolderFileContent).mockResolvedValue({
			path: conflictCopyName,
			name: conflictCopyName,
			type: 'doc',
			content: 'conflict body'
		});

		const data = await load({
			params: { slug: 'test-slug', path: conflictCopyName },
			cookies: makeCookies(),
			url: makeUrl()
		});

		expect(data.file.path).toBe(conflictCopyName);
		expect(data.contentHtml).toContain('conflict body');
	});

	it('matches an item whose name has a space via the hyphenated-path branch, with % surviving the round trip', async () => {
		const originalName = '50% done.md';
		// What params.path actually looks like once SvelteKit has decoded the
		// href slugifyPath produced ("50%25-done.md" on the wire).
		const decodedRoutePath = '50%-done.md';

		vi.mocked(api.getShareBySlug).mockResolvedValue(
			makeShare({
				web_folder_items: [{ path: originalName, name: originalName, type: 'doc' }]
			})
		);
		vi.mocked(api.getFolderFileContent).mockResolvedValue({
			path: originalName,
			name: originalName,
			type: 'doc',
			content: 'progress body'
		});

		const data = await load({
			params: { slug: 'test-slug', path: decodedRoutePath },
			cookies: makeCookies(),
			url: makeUrl()
		});

		expect(data.file.path).toBe(originalName);
		expect(data.contentHtml).toContain('progress body');
	});

	it('404s when no item matches either the exact or hyphenated form', async () => {
		vi.mocked(api.getShareBySlug).mockResolvedValue(
			makeShare({ web_folder_items: [{ path: 'Notes/Doc.md', name: 'Doc.md', type: 'doc' }] })
		);

		await expect(
			load({
				params: { slug: 'test-slug', path: 'Notes/Missing.md' },
				cookies: makeCookies(),
				url: makeUrl()
			})
		).rejects.toMatchObject({ status: 404 });
	});
});
