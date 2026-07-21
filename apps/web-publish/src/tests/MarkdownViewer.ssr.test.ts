// ---------------------------------------------------------------------------
// MarkdownViewer — server-side rendering (TR-37)
// ---------------------------------------------------------------------------
// The body used to render only in onMount/$effect (both browser-only), so a
// non-JS crawler/AI scraper got an HTML shell with no content. Proves the
// actual SSR HTML output directly via Svelte's server renderer, not just the
// load() data shape.
// ---------------------------------------------------------------------------

import { describe, it, expect } from 'vitest';
import { render } from 'svelte/server';
import MarkdownViewer from '../lib/components/MarkdownViewer.svelte';

describe('MarkdownViewer SSR output', () => {
	it('renders the document body in SSR when initialHtml is provided', () => {
		const { body } = render(MarkdownViewer, {
			props: {
				content: '# Hello\n\nWorld.',
				initialHtml: '<h1>Hello</h1><p>World.</p>'
			}
		});

		expect(body).toContain('<h1>Hello</h1>');
		expect(body).toContain('World.');
		expect(body).not.toContain('markdown-loading');
	});

	it('falls back to the loading skeleton in SSR when no initialHtml is given', () => {
		const { body } = render(MarkdownViewer, {
			props: {
				content: '# Hello\n\nWorld.'
			}
		});

		expect(body).toContain('markdown-loading');
		expect(body).not.toContain('<h1>Hello</h1>');
	});
});
