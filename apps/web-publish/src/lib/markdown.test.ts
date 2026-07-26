import { describe, it, expect } from 'vitest';
import { renderMarkdown, extractTitle, extractDescription, estimateReadingTime } from './markdown.js';

// ---------------------------------------------------------------------------
// DOMPurify sanitisation — XSS / injection vectors
// ---------------------------------------------------------------------------

describe('renderMarkdown — XSS sanitisation', () => {
	it('strips <script> tags', async () => {
		const html = await renderMarkdown('<script>alert("xss")</script>');
		expect(html).not.toContain('<script');
		expect(html).not.toContain('alert(');
	});

	it('strips onerror= event attributes', async () => {
		const html = await renderMarkdown('<img src="x" onerror="alert(1)">');
		expect(html).not.toContain('onerror');
	});

	it('strips javascript: URLs in anchor href', async () => {
		// GFM auto-link won't linkify javascript: but explicit HTML might pass through
		const html = await renderMarkdown('[click me](javascript:alert(1))');
		// DOMPurify must strip the href or the entire anchor
		expect(html).not.toContain('javascript:');
	});

	it('strips onclick= on inline HTML', async () => {
		const html = await renderMarkdown('<button onclick="evil()">Click</button>');
		expect(html).not.toContain('onclick');
	});

	it('strips <iframe> tags', async () => {
		const html = await renderMarkdown('<iframe src="https://evil.com"></iframe>');
		expect(html).not.toContain('<iframe');
	});

	it('strips <object> tags', async () => {
		const html = await renderMarkdown('<object data="evil.swf"></object>');
		expect(html).not.toContain('<object');
	});

	it('strips SVG with onload injection', async () => {
		// SVG tags are allowed for KaTeX/mermaid, but onload must be removed
		const html = await renderMarkdown('<svg onload="alert(1)"><circle r="10"/></svg>');
		expect(html).not.toContain('onload');
	});

	it('strips SVG foreignObject script injection', async () => {
		const payload = '<svg><foreignObject><script>alert(1)</script></foreignObject></svg>';
		const html = await renderMarkdown(payload);
		expect(html).not.toContain('<script');
	});

	it('strips javascript: href — more dangerous than data: URIs', async () => {
		// javascript: in href is the primary XSS anchor vector; data:text/html in img src
		// is not executable in modern browsers (browsers reject it as a broken image).
		const html = await renderMarkdown('[xss](javascript:alert(1))');
		expect(html).not.toContain('javascript:');
	});
});

// ---------------------------------------------------------------------------
// Legitimate markdown — preserved after sanitisation
// ---------------------------------------------------------------------------

describe('renderMarkdown — legitimate content preserved', () => {
	it('renders bold and italic', async () => {
		const html = await renderMarkdown('**bold** and _italic_');
		expect(html).toContain('<strong>bold</strong>');
		expect(html).toContain('<em>italic</em>');
	});

	it('renders links with http: href', async () => {
		const html = await renderMarkdown('[Example](https://example.com)');
		expect(html).toContain('href="https://example.com"');
	});

	it('renders fenced code block', async () => {
		const html = await renderMarkdown('```js\nconsole.log("hi");\n```');
		expect(html).toContain('<code');
		// hljs wraps identifiers in spans, so check for the base text split across them
		expect(html).toContain('console');
		expect(html).toContain('log');
	});

	it('renders headings', async () => {
		const html = await renderMarkdown('# Hello World');
		expect(html).toContain('<h1');
		expect(html).toContain('Hello World');
	});

	it('renders images with relative path', async () => {
		const html = await renderMarkdown('![alt](image.png)', { slug: 'my-share' });
		expect(html).toContain('<img');
	});

	it('renders blockquote callout', async () => {
		const html = await renderMarkdown('> [!note] Important\n> Content here');
		expect(html).toContain('callout');
	});

	it('renders highlight ==text==', async () => {
		const html = await renderMarkdown('==highlighted==');
		expect(html).toContain('<mark>highlighted</mark>');
	});

	it('renders strikethrough', async () => {
		const html = await renderMarkdown('~~deleted~~');
		expect(html).toContain('<del>');
	});

	it('renders table', async () => {
		const html = await renderMarkdown('| A | B |\n|---|---|\n| 1 | 2 |');
		expect(html).toContain('<table');
		expect(html).toContain('<td');
	});
});

// ---------------------------------------------------------------------------
// extractTitle
// ---------------------------------------------------------------------------

describe('extractTitle', () => {
	it('extracts title from YAML frontmatter', () => {
		const md = '---\ntitle: My Doc\n---\nContent';
		expect(extractTitle(md)).toBe('My Doc');
	});

	it('extracts title from first h1', () => {
		const md = '# Hello World\nContent';
		expect(extractTitle(md)).toBe('Hello World');
	});

	it('uses fallback when no title found', () => {
		expect(extractTitle('no heading here', 'fallback.md')).toBe('fallback.md');
	});

	it('strips frontmatter before looking for h1', () => {
		const md = '---\nauthor: Bob\n---\n# Real Title\nContent';
		expect(extractTitle(md)).toBe('Real Title');
	});
});

// ---------------------------------------------------------------------------
// extractDescription
// ---------------------------------------------------------------------------

describe('extractDescription', () => {
	it('extracts description from YAML frontmatter', () => {
		const md = '---\ndescription: A short desc\n---\nContent';
		expect(extractDescription(md)).toBe('A short desc');
	});

	it('extracts first paragraph when no frontmatter', () => {
		const md = 'Some introductory text here.';
		expect(extractDescription(md)).toContain('introductory text');
	});

	it('truncates to 160 chars', () => {
		const long = 'A'.repeat(200);
		const result = extractDescription(long);
		expect(result.length).toBeLessThanOrEqual(160);
		expect(result).toMatch(/\.\.\.$/);
	});

	it('uses fallback when content is empty', () => {
		expect(extractDescription('', 'fallback desc')).toBe('fallback desc');
	});
});

// ---------------------------------------------------------------------------
// estimateReadingTime
// ---------------------------------------------------------------------------

describe('estimateReadingTime', () => {
	it('returns at least 1 minute for any content', () => {
		expect(estimateReadingTime('hello world')).toBeGreaterThanOrEqual(1);
	});

	it('estimates proportionally to word count', () => {
		// 200 words @ 200 wpm = 1 min
		const text = Array(200).fill('word').join(' ');
		expect(estimateReadingTime(text)).toBe(1);
	});

	it('strips frontmatter before counting', () => {
		const md = '---\ntitle: x\n---\nContent only';
		const noFm = 'Content only';
		// With frontmatter stripped the reading time should be the same as without it
		expect(estimateReadingTime(md)).toBe(estimateReadingTime(noFm));
	});
});

// ---------------------------------------------------------------------------
// Concurrent-call isolation (TR-37)
// ---------------------------------------------------------------------------
// renderMarkdown() now runs inside SvelteKit's server load(), where one Node
// process serves many requests concurrently. Math and embed/slug resolution
// used to go through module-level mutable state (mathStore, _renderContext)
// that a second concurrent call would reset/overwrite mid-render, bleeding
// one document's content into another's. These prove two overlapping calls
// never cross-contaminate.
// ---------------------------------------------------------------------------

describe('renderMarkdown — concurrent-call isolation', () => {
	it('does not cross-contaminate math between two concurrent renders', async () => {
		const docA = '$$x^2$$';
		const docB = '$$y^3$$';

		const [htmlA, htmlB] = await Promise.all([
			renderMarkdown(docA),
			renderMarkdown(docB)
		]);

		expect(htmlA).toContain('x^2');
		expect(htmlA).not.toContain('y^3');
		expect(htmlB).toContain('y^3');
		expect(htmlB).not.toContain('x^2');
	});

	it('does not cross-contaminate embed/slug resolution between two concurrent renders', async () => {
		const doc = '![[photo.png]]';

		const [htmlA, htmlB] = await Promise.all([
			renderMarkdown(doc, { slug: 'slug-A', folderItems: [] }),
			renderMarkdown(doc, { slug: 'slug-B', folderItems: [] })
		]);

		expect(htmlA).toContain('/slug-A/_assets/photo.png');
		expect(htmlA).not.toContain('/slug-B/_assets/photo.png');
		expect(htmlB).toContain('/slug-B/_assets/photo.png');
		expect(htmlB).not.toContain('/slug-A/_assets/photo.png');
	});

	it('does not cross-contaminate across many concurrent renders with mixed content', async () => {
		const inputs = Array.from({ length: 10 }, (_, i) => ({
			slug: `slug-${i}`,
			math: `${i}^2`
		}));

		const results = await Promise.all(
			inputs.map((input) =>
				renderMarkdown(`![[photo.png]]\n\n$$${input.math}$$`, { slug: input.slug, folderItems: [] })
			)
		);

		results.forEach((html, i) => {
			expect(html).toContain(`/slug-${i}/_assets/photo.png`);
			expect(html).toContain(inputs[i].math);
		});
	});
});
