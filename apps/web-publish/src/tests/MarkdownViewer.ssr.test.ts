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
import RenderedContentStyles from '../lib/components/RenderedContentStyles.svelte';

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

	// The CSP is style-src 'self': the CDN stylesheet links this replaced were
	// blocked on every page (unthemed code, unstyled formulas, a console error
	// each). Self-hosted CSS must be linked exactly when the document needs it
	// — and only then, so a plain page pays for neither (#cee667d8).
	it('links the self-hosted KaTeX stylesheet only when the body has math', () => {
		const withMath = render(MarkdownViewer, {
			props: { content: 'x', initialHtml: '<p><span class="katex">x</span></p>' }
		}).head;
		const without = render(MarkdownViewer, {
			props: { content: 'x', initialHtml: '<p>plain</p>' }
		}).head;

		// (vitest resolves `?url` CSS imports to '' — the real hashed URLs are
		// asserted against the production build in the perf/CSP verification.)
		expect(withMath.match(/<link rel="stylesheet"/g)).toHaveLength(1);
		expect(without).not.toContain('<link');
	});

	it('links the self-hosted highlight.js theme only when the body has code', () => {
		const withCode = render(MarkdownViewer, {
			props: { content: 'x', initialHtml: '<pre><code class="hljs language-js">x</code></pre>' }
		}).head;
		const without = render(MarkdownViewer, {
			props: { content: 'x', initialHtml: '<p>plain</p>' }
		}).head;

		// one theme, no colour-scheme switch: --code-bg is dark in both schemes
		expect(withCode.match(/<link rel="stylesheet"/g)).toHaveLength(1);
		expect(withCode).not.toContain('prefers-color-scheme');
		expect(withCode).not.toMatch(/https?:\/\//); // nothing cross-origin
		expect(without).not.toContain('<link');
	});

	// Shared by MarkdownViewer and EditableMarkdownViewer (an owner editing a
	// share must not see unthemed code/formulas the reader does not).
	it('RenderedContentStyles links exactly the sheets the html needs', () => {
		const head = (html: string) => render(RenderedContentStyles, { props: { html } }).head;
		const links = (h: string) => (h.match(/<link rel="stylesheet"/g) ?? []).length;

		expect(links(head('<p>plain</p>'))).toBe(0);
		expect(links(head('<span class="katex">x</span>'))).toBe(1);
		expect(links(head('<code class="hljs language-js">x</code>'))).toBe(1);
		expect(links(head('<span class="katex">x</span><code class="hljs language-js">x</code>'))).toBe(2);
	});
});
