/**
 * Markdown parsing and rendering utilities.
 */

import { Marked } from 'marked';
import { markedHighlight } from 'marked-highlight';
import hljs from 'highlight.js';
import DOMPurify from 'isomorphic-dompurify';

// Configure marked with syntax highlighting
const marked = new Marked(
	markedHighlight({
		langPrefix: 'hljs language-',
		highlight(code, lang) {
			const language = hljs.getLanguage(lang) ? lang : 'plaintext';
			return hljs.highlight(code, { language }).value;
		}
	})
);

// Configure marked options for GFM support
marked.setOptions({
	gfm: true, // GitHub Flavored Markdown
	breaks: false, // Don't convert \n to <br>
	pedantic: false,
	headerIds: true,
	mangle: false
});

/**
 * Parse and render markdown to HTML.
 * Sanitizes HTML output to prevent XSS.
 */
export async function renderMarkdown(markdown: string): Promise<string> {
	// Parse markdown to HTML
	const rawHtml = await marked.parse(markdown);

	// Sanitize HTML to prevent XSS
	const sanitizedHtml = DOMPurify.sanitize(rawHtml, {
		ALLOWED_TAGS: [
			'h1',
			'h2',
			'h3',
			'h4',
			'h5',
			'h6',
			'p',
			'a',
			'ul',
			'ol',
			'li',
			'blockquote',
			'code',
			'pre',
			'strong',
			'em',
			'del',
			'table',
			'thead',
			'tbody',
			'tr',
			'th',
			'td',
			'br',
			'hr',
			'img',
			'span',
			'div'
		],
		ALLOWED_ATTR: ['href', 'title', 'src', 'alt', 'class', 'id', 'target', 'rel'],
		ALLOW_DATA_ATTR: false
	});

	return sanitizedHtml;
}

/**
 * Extract title from markdown (first h1 heading or filename).
 */
export function extractTitle(markdown: string, fallback: string = 'Untitled'): string {
	// Look for first h1 heading
	const h1Match = markdown.match(/^#\s+(.+)$/m);
	if (h1Match) {
		return h1Match[1].trim();
	}

	// Fallback to filename or default
	return fallback;
}

/**
 * Estimate reading time in minutes.
 */
export function estimateReadingTime(markdown: string): number {
	const wordsPerMinute = 200;
	const words = markdown.split(/\s+/).length;
	return Math.ceil(words / wordsPerMinute);
}
