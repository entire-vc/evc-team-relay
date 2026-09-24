/**
 * Lightweight markdown metadata helpers — title/description/reading-time
 * extraction via plain string/regex ops, no markdown parser or renderer.
 *
 * Deliberately kept in its own module, separate from $lib/markdown: these
 * are imported directly by client route components (+page.svelte) for page
 * <title>/meta tags, and $lib/markdown pulls in marked/highlight.js/katex/
 * dompurify. A single shared file would force Rollup to bundle all of that
 * into the client's main chunk too — a static importer anywhere prevents a
 * dynamic import() elsewhere from splitting the module out (#cee667d8).
 */

/**
 * Strip YAML frontmatter (---\n...\n---) from start of document.
 */
export function stripFrontmatter(text: string): string {
	return text.replace(/^---\n[\s\S]*?\n---\n?/, '');
}

/**
 * Strip Obsidian comments (%%...%%) from content.
 * Handles both inline and multiline comments.
 */
export function stripComments(text: string): string {
	return text.replace(/%%[\s\S]*?%%/g, '');
}

/**
 * Extract title from markdown (first h1 heading, or YAML title, or filename).
 * Handles frontmatter stripping.
 */
export function extractTitle(markdown: string, fallback: string = 'Untitled'): string {
	// Try to extract title from YAML frontmatter first
	const fmMatch = markdown.match(/^---\n([\s\S]*?)\n---/);
	if (fmMatch) {
		const titleMatch = fmMatch[1].match(/^title:\s*(.+)$/m);
		if (titleMatch) {
			// Remove quotes if present
			return titleMatch[1].trim().replace(/^["']|["']$/g, '');
		}
	}

	// Strip frontmatter before looking for h1
	const stripped = stripFrontmatter(markdown);

	// Look for first h1 heading
	const h1Match = stripped.match(/^#\s+(.+)$/m);
	if (h1Match) {
		return h1Match[1].trim();
	}

	// Fallback to filename or default
	return fallback;
}

/**
 * Extract description from markdown for SEO meta tags.
 * Priority: frontmatter description > first paragraph text (up to 160 chars).
 */
export function extractDescription(markdown: string, fallback: string = ''): string {
	// Try frontmatter description first
	const fmMatch = markdown.match(/^---\n([\s\S]*?)\n---/);
	if (fmMatch) {
		const descMatch = fmMatch[1].match(/^description:\s*(.+)$/m);
		if (descMatch) {
			return descMatch[1].trim().replace(/^["']|["']$/g, '');
		}
	}

	// Strip frontmatter and find first paragraph of plain text
	const stripped = stripFrontmatter(markdown);
	// Remove headings, code blocks, images, links syntax, HTML tags
	const lines = stripped.split('\n');
	const textLines: string[] = [];
	let inCodeBlock = false;

	for (const line of lines) {
		if (line.startsWith('```')) {
			inCodeBlock = !inCodeBlock;
			continue;
		}
		if (inCodeBlock) continue;
		if (line.startsWith('#')) continue;
		if (line.startsWith('![[')) continue;
		if (line.startsWith('![')) continue;
		if (line.startsWith('---')) continue;
		if (line.startsWith('> [!')) continue; // callout headers
		const trimmed = line.trim();
		if (trimmed.length === 0) {
			if (textLines.length > 0) break; // stop at first blank line after content
			continue;
		}
		// Clean markdown syntax from text
		const cleaned = trimmed
			.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1') // [text](url) → text
			.replace(/\[\[([^\]|]+)(?:\|[^\]]+)?\]\]/g, '$1') // [[link|text]] → link
			.replace(/[*_~`]+/g, '') // bold, italic, strikethrough, code
			.replace(/==([^=]+)==/g, '$1') // highlights
			.replace(/<[^>]+>/g, ''); // HTML tags
		textLines.push(cleaned);
	}

	const description = textLines.join(' ').trim();
	if (description.length > 160) {
		return description.substring(0, 157) + '...';
	}
	return description || fallback;
}

/**
 * Estimate reading time in minutes.
 * Strips frontmatter and comments before counting.
 */
export function estimateReadingTime(markdown: string): number {
	const wordsPerMinute = 200;
	let text = stripFrontmatter(markdown);
	text = stripComments(text);
	const words = text.split(/\s+/).filter((w) => w.length > 0).length;
	return Math.ceil(words / wordsPerMinute);
}
