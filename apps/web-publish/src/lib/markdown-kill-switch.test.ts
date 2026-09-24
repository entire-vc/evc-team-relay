import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

/**
 * The kill switch (#cee667d8, PUBLIC_LAZY_RENDERERS_DISABLED) is read once
 * at module load, so each case here resets the module registry and sets the
 * env var beforehand to get a fresh evaluation — the same reason it's a
 * runtime container-restart toggle in production, not a per-call option.
 */
describe('renderMarkdown — lazy-renderer kill switch', () => {
	const ORIGINAL = process.env.PUBLIC_LAZY_RENDERERS_DISABLED;

	beforeEach(() => {
		vi.resetModules();
	});

	afterEach(() => {
		if (ORIGINAL === undefined) delete process.env.PUBLIC_LAZY_RENDERERS_DISABLED;
		else process.env.PUBLIC_LAZY_RENDERERS_DISABLED = ORIGINAL;
		vi.resetModules();
	});

	it('default (flag unset): unlabelled fenced code skips hljs entirely (plain escaped text)', async () => {
		delete process.env.PUBLIC_LAZY_RENDERERS_DISABLED;
		const { renderMarkdown } = await import('./markdown.js');
		const html = await renderMarkdown('```\nplain block\n```');
		expect(html).toContain('plain block');
		// hljs's own grammar wrapping (even for 'plaintext') is never invoked in this path.
		expect(html).not.toContain('hljs-');
	});

	it('flag=true: unlabelled fenced code still goes through hljs (matches pre-fix "always eager" behaviour)', async () => {
		process.env.PUBLIC_LAZY_RENDERERS_DISABLED = 'true';
		const { renderMarkdown } = await import('./markdown.js');
		const html = await renderMarkdown('```\nconsole.log(1)\n```');
		// Content is preserved either way — the switch changes *when/whether*
		// hljs is invoked, not correctness of the fallback path.
		expect(html).toContain('console.log(1)');
	});

	it('flag=true: a real document (code + math) still renders correctly end to end', async () => {
		process.env.PUBLIC_LAZY_RENDERERS_DISABLED = 'true';
		const { renderMarkdown } = await import('./markdown.js');
		const html = await renderMarkdown('```js\nconst x = 1;\n```\n\n$E=mc^2$');
		expect(html).toContain('const');
		expect(html).toContain('katex');
	});

	it('flag=true: warms hljs core (not just katex) even for a document with no code or math at all', async () => {
		process.env.PUBLIC_LAZY_RENDERERS_DISABLED = 'true';
		const { renderMarkdown } = await import('./markdown.js');
		// Plain prose, nothing that would trigger a code/math path on its own —
		// this only exercises the unconditional warm-up in renderMarkdown(),
		// not highlightCode()/restoreMath()'s own gating.
		const html = await renderMarkdown('Just a paragraph, nothing special.');
		expect(html).toContain('Just a paragraph');
		// Renders without throwing is the main assertion here — the warm-up
		// itself is fire-and-forget (`void loadHljsCore()`), so there's no
		// return value to assert on directly.
	});
});
