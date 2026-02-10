/**
 * Markdown parsing and rendering utilities.
 *
 * Supports Obsidian-flavored markdown features:
 * - YAML frontmatter stripping
 * - Comments (%%...%%)
 * - Highlights (==text==)
 * - Wikilinks ([[Note]], [[Note|Display]])
 * - Image/media embeds (![[image.png]])
 * - Callouts (> [!type] Title)
 * - Math/LaTeX ($...$ and $$...$$)
 * - Mermaid diagrams (```mermaid)
 * - Footnotes ([^1] and ^[inline])
 * - Tags (#tag, #nested/tag)
 * - Task lists with custom checkboxes ([x], [/], [-], etc.)
 */

import { Marked, type Token, type Tokens } from 'marked';
import { markedHighlight } from 'marked-highlight';
import markedFootnote from 'marked-footnote';
import hljs from 'highlight.js';
import katex from 'katex';
import DOMPurify from 'isomorphic-dompurify';

// ---------------------------------------------------------------------------
// HTML escaping utility (defense-in-depth before DOMPurify)
// ---------------------------------------------------------------------------

function escapeHtml(str: string): string {
	return str
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;');
}

// ---------------------------------------------------------------------------
// Math placeholder system
// ---------------------------------------------------------------------------

/** Unique prefix that won't appear in normal content */
const MATH_PLACEHOLDER_PREFIX = '\x00MATH_';
const MATH_PLACEHOLDER_SUFFIX = '\x00';

/** Store for math expressions extracted during preprocessing */
let mathStore: Map<string, { expression: string; displayMode: boolean }> = new Map();
let mathCounter = 0;

function resetMathStore(): void {
	mathStore = new Map();
	mathCounter = 0;
}

function createMathPlaceholder(expression: string, displayMode: boolean): string {
	const id = `${MATH_PLACEHOLDER_PREFIX}${mathCounter++}${MATH_PLACEHOLDER_SUFFIX}`;
	mathStore.set(id, { expression, displayMode });
	return id;
}

// ---------------------------------------------------------------------------
// Preprocessing pipeline
// ---------------------------------------------------------------------------

/**
 * Strip Obsidian comments (%%...%%) from content.
 * Handles both inline and multiline comments.
 */
function stripComments(text: string): string {
	return text.replace(/%%[\s\S]*?%%/g, '');
}

/**
 * Strip YAML frontmatter (---\n...\n---) from start of document.
 */
function stripFrontmatter(text: string): string {
	return text.replace(/^---\n[\s\S]*?\n---\n?/, '');
}

/**
 * Protect math expressions from marked parsing by replacing them with placeholders.
 * Must be called AFTER stripping comments and frontmatter but BEFORE marked.parse().
 *
 * Order: $$...$$ first (display), then $...$ (inline).
 * Skip anything inside code fences or inline code.
 */
function protectMath(text: string): string {
	// First, protect code blocks and inline code so we don't match $ inside them
	const codeBlocks: { placeholder: string; content: string }[] = [];
	let codeCounter = 0;

	// Protect fenced code blocks (```...```)
	let result = text.replace(/```[\s\S]*?```/g, (match) => {
		const placeholder = `\x00CODE_BLOCK_${codeCounter++}\x00`;
		codeBlocks.push({ placeholder, content: match });
		return placeholder;
	});

	// Protect inline code (`...`)
	result = result.replace(/`[^`\n]+`/g, (match) => {
		const placeholder = `\x00CODE_BLOCK_${codeCounter++}\x00`;
		codeBlocks.push({ placeholder, content: match });
		return placeholder;
	});

	// Replace display math ($$...$$) - can be multiline
	result = result.replace(/\$\$([\s\S]+?)\$\$/g, (_match, expr: string) => {
		return createMathPlaceholder(expr.trim(), true);
	});

	// Replace inline math ($...$) - single line only, not empty
	// Negative lookbehind for \ to avoid matching \$
	// Must not start or end with space (Obsidian behavior)
	result = result.replace(/(?<![\\$])\$([^\s$](?:[^$]*[^\s$])?)\$(?!\d)/g, (_match, expr: string) => {
		return createMathPlaceholder(expr, false);
	});

	// Restore code blocks
	for (const { placeholder, content } of codeBlocks) {
		result = result.replace(placeholder, content);
	}

	return result;
}

/**
 * Full preprocessing pipeline.
 * Returns cleaned markdown ready for marked.parse().
 */
function preprocessMarkdown(raw: string): string {
	let text = raw;
	text = stripFrontmatter(text);
	text = stripComments(text);
	text = protectMath(text);
	return text;
}

// ---------------------------------------------------------------------------
// Post-processing: restore math placeholders with KaTeX HTML
// ---------------------------------------------------------------------------

/**
 * Replace math placeholders with rendered KaTeX HTML.
 */
function restoreMath(html: string): string {
	for (const [placeholder, { expression, displayMode }] of mathStore.entries()) {
		try {
			const rendered = katex.renderToString(expression, {
				displayMode,
				throwOnError: false,
				output: 'htmlAndMathml',
				trust: false
			});

			if (displayMode) {
				html = html.replace(
					placeholder,
					`<div class="katex-display">${rendered}</div>`
				);
			} else {
				html = html.replace(placeholder, rendered);
			}
		} catch {
			// If KaTeX fails, show the raw expression
			const escaped = expression
				.replace(/&/g, '&amp;')
				.replace(/</g, '&lt;')
				.replace(/>/g, '&gt;');
			const wrapper = displayMode
				? `<div class="katex-error katex-display">$$${escaped}$$</div>`
				: `<span class="katex-error">$${escaped}$</span>`;
			html = html.replace(placeholder, wrapper);
		}
	}
	return html;
}

// ---------------------------------------------------------------------------
// Marked extensions: Highlights
// ---------------------------------------------------------------------------

/**
 * Inline extension for ==highlighted text==.
 */
const highlightExtension = {
	name: 'highlight' as const,
	level: 'inline' as const,
	start(src: string) {
		return src.indexOf('==');
	},
	tokenizer(src: string) {
		const match = src.match(/^==([^=]+)==/);
		if (match) {
			return {
				type: 'highlight',
				raw: match[0],
				text: match[1]
			};
		}
		return undefined;
	},
	renderer(token: { text: string }) {
		return `<mark>${escapeHtml(token.text)}</mark>`;
	}
};

// ---------------------------------------------------------------------------
// Marked extensions: Wikilinks
// ---------------------------------------------------------------------------

/**
 * Inline extension for [[wikilinks]] and [[target|display]].
 * Does NOT match ![[embeds]].
 */
const wikilinkExtension = {
	name: 'wikilink' as const,
	level: 'inline' as const,
	start(src: string) {
		// Find [[ that is NOT preceded by !
		const idx = src.indexOf('[[');
		if (idx === -1) return -1;
		if (idx > 0 && src[idx - 1] === '!') {
			// Skip this one, find the next
			const rest = src.slice(idx + 2);
			const next = rest.indexOf('[[');
			if (next === -1) return -1;
			return idx + 2 + next;
		}
		return idx;
	},
	tokenizer(src: string) {
		// Don't match if preceded by !
		const match = src.match(/^\[\[([^\]]+)\]\]/);
		if (match) {
			const content = match[1];
			let target: string;
			let display: string;

			if (content.includes('|')) {
				const parts = content.split('|');
				target = parts[0].trim();
				display = parts.slice(1).join('|').trim();
			} else {
				target = content.trim();
				display = target;
			}

			return {
				type: 'wikilink',
				raw: match[0],
				target,
				display
			};
		}
		return undefined;
	},
	renderer(token: { target: string; display: string }) {
		const { target, display } = token;

		// Internal heading link: [[#Heading]]
		if (target.startsWith('#')) {
			const slug = target
				.slice(1)
				.toLowerCase()
				.replace(/[^\w\s-]/g, '')
				.replace(/\s+/g, '-');
			const text = display.startsWith('#') ? display.slice(1) : display;
			return `<a class="obsidian-wikilink obsidian-wikilink-heading" href="#${escapeHtml(slug)}">${escapeHtml(text)}</a>`;
		}

		// Regular wikilink: non-functional in web-publish (no onclick for XSS safety)
		return `<a class="obsidian-wikilink" href="#" data-wikilink-disabled>${escapeHtml(display)}</a>`;
	}
};

// ---------------------------------------------------------------------------
// Marked extensions: Tags
// ---------------------------------------------------------------------------

/**
 * Inline extension for #tags including nested tags like #project/urgent.
 * Must NOT match:
 * - Inside code blocks or inline code (handled by marked's parsing order)
 * - URL anchors (e.g., https://example.com#anchor)
 * - Heading references inside wikilinks (e.g., [[#heading]])
 */
const tagExtension = {
	name: 'obsidianTag' as const,
	level: 'inline' as const,
	start(src: string) {
		let idx = src.indexOf('#');
		while (idx !== -1) {
			// Skip if preceded by : or / (URL context like https://example.com#anchor)
			if (idx > 0 && (src[idx - 1] === ':' || src[idx - 1] === '/')) {
				idx = src.indexOf('#', idx + 1);
				continue;
			}
			// Must be followed by a letter (not digit, not space)
			if (idx + 1 < src.length && /[a-zA-Z]/.test(src[idx + 1])) {
				return idx;
			}
			idx = src.indexOf('#', idx + 1);
		}
		return -1;
	},
	tokenizer(src: string) {
		// Pattern: #word (can contain letters, numbers, /, -)
		// Must start with letter after #
		const match = src.match(/^#([a-zA-Z][a-zA-Z0-9/_-]*)/);
		if (match) {
			return {
				type: 'obsidianTag',
				raw: match[0],
				tag: match[1]
			};
		}
		return undefined;
	},
	renderer(token: { tag: string }) {
		return `<span class="obsidian-tag">#${escapeHtml(token.tag)}</span>`;
	}
};

// ---------------------------------------------------------------------------
// Marked extensions: Embeds
// ---------------------------------------------------------------------------

/**
 * Inline extension for ![[embed]] syntax.
 */
const embedExtension = {
	name: 'obsidianEmbed' as const,
	level: 'inline' as const,
	start(src: string) {
		return src.indexOf('![[');
	},
	tokenizer(src: string) {
		const match = src.match(/^!\[\[([^\]]+)\]\]/);
		if (match) {
			const content = match[1];
			let target: string;
			let size: string | null = null;

			if (content.includes('|')) {
				const parts = content.split('|');
				target = parts[0].trim();
				size = parts.slice(1).join('|').trim();
			} else {
				target = content.trim();
			}

			return {
				type: 'obsidianEmbed',
				raw: match[0],
				target,
				size
			};
		}
		return undefined;
	},
	renderer(token: { target: string; size: string | null }) {
		const { target, size } = token;
		const safeTarget = escapeHtml(target);
		const safeSize = size ? escapeHtml(size) : null;
		const ext = target.split('.').pop()?.toLowerCase() || '';
		const imageExts = ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp', 'ico'];
		const videoExts = ['mp4', 'webm', 'ogv', 'mov'];
		const audioExts = ['mp3', 'wav', 'ogg', 'flac', 'm4a'];

		// Image embeds with asset proxy
		if (imageExts.includes(ext)) {
			if (_renderContext.slug) {
				const styleAttr = safeSize ? ` style="max-width: ${safeSize}px"` : '';
				return `<img class="obsidian-embed-image" src="/${_renderContext.slug}/_assets/${safeTarget}" alt="${safeTarget}"${styleAttr} />`;
			}
			// Fallback placeholder without slug
			const sizeInfo = safeSize ? ` (${safeSize}px)` : '';
			return `<div class="obsidian-embed obsidian-embed-image"><span class="obsidian-embed-icon">&#128444;</span> Image: <strong>${safeTarget}</strong>${sizeInfo}</div>`;
		}

		// Video/audio embeds (placeholder for now)
		if (videoExts.includes(ext)) {
			return `<div class="obsidian-embed obsidian-embed-video"><span class="obsidian-embed-icon">&#127909;</span> Video: <strong>${safeTarget}</strong></div>`;
		}

		if (audioExts.includes(ext)) {
			return `<div class="obsidian-embed obsidian-embed-audio"><span class="obsidian-embed-icon">&#127925;</span> Audio: <strong>${safeTarget}</strong></div>`;
		}

		// Note embeds - show enhanced placeholder if found in folder
		// TODO: Inline note rendering requires async pre-processing pass
		if (_renderContext.folderItems) {
			const noteItem = _renderContext.folderItems.find(
				item => item.path === target || item.path === `${target}.md`
			);

			if (noteItem) {
				const noteName = escapeHtml(noteItem.name || target);
				// Show styled placeholder for found notes
				return `<div class="obsidian-embed obsidian-embed-note obsidian-embed-note-found">
					<span class="obsidian-embed-icon">&#128196;</span>
					<span class="obsidian-embed-note-name">${noteName}</span>
					<span class="obsidian-embed-note-hint">(in this folder)</span>
				</div>`;
			}
		}

		// Note embed not found - placeholder
		return `<div class="obsidian-embed obsidian-embed-note"><span class="obsidian-embed-icon">&#128196;</span> Embedded note: <strong>${safeTarget}</strong></div>`;
	}
};

// ---------------------------------------------------------------------------
// Marked extensions: Callouts
// ---------------------------------------------------------------------------

/** Callout type metadata: icon (Unicode) and CSS color class suffix */
const CALLOUT_TYPES: Record<string, { icon: string; color: string }> = {
	note: { icon: '\u270F\uFE0F', color: 'blue' },
	info: { icon: '\u2139\uFE0F', color: 'blue' },
	todo: { icon: '\u2611\uFE0F', color: 'blue' },
	abstract: { icon: '\uD83D\uDCCB', color: 'teal' },
	summary: { icon: '\uD83D\uDCCB', color: 'teal' },
	tldr: { icon: '\uD83D\uDCCB', color: 'teal' },
	tip: { icon: '\uD83D\uDD25', color: 'cyan' },
	hint: { icon: '\uD83D\uDD25', color: 'cyan' },
	important: { icon: '\uD83D\uDD25', color: 'cyan' },
	success: { icon: '\u2705', color: 'green' },
	check: { icon: '\u2705', color: 'green' },
	done: { icon: '\u2705', color: 'green' },
	question: { icon: '\u2753', color: 'yellow' },
	help: { icon: '\u2753', color: 'yellow' },
	faq: { icon: '\u2753', color: 'yellow' },
	warning: { icon: '\u26A0\uFE0F', color: 'orange' },
	caution: { icon: '\u26A0\uFE0F', color: 'orange' },
	attention: { icon: '\u26A0\uFE0F', color: 'orange' },
	failure: { icon: '\u274C', color: 'red' },
	fail: { icon: '\u274C', color: 'red' },
	missing: { icon: '\u274C', color: 'red' },
	danger: { icon: '\u26D4', color: 'red-dark' },
	error: { icon: '\u26D4', color: 'red-dark' },
	bug: { icon: '\uD83D\uDC1B', color: 'red' },
	example: { icon: '\uD83D\uDCDD', color: 'purple' },
	quote: { icon: '\u275D', color: 'gray' },
	cite: { icon: '\u275D', color: 'gray' }
};

/**
 * Walk tokens to detect callout blockquotes and annotate them.
 * A callout blockquote has its first text line matching [!type].
 */
function walkTokensForCallouts(token: Token): void {
	if (token.type !== 'blockquote') return;

	const bq = token as Tokens.Blockquote;
	if (!bq.tokens || bq.tokens.length === 0) return;

	// Get the raw text of the first paragraph
	const firstChild = bq.tokens[0];
	if (firstChild.type !== 'paragraph') return;

	const para = firstChild as Tokens.Paragraph;
	const rawText = para.raw || para.text || '';

	// Check for callout pattern: [!type] or [!type]+ or [!type]-
	const calloutMatch = rawText.match(
		/^\[!(\w+)\]([-+])?\s*(.*)/s
	);

	if (!calloutMatch) return;

	const calloutType = calloutMatch[1].toLowerCase();
	const foldChar = calloutMatch[2] || ''; // '+', '-', or ''
	const titleAndRest = calloutMatch[3] || '';

	// Extract title (first line after [!type]) and remaining content
	const titleLines = titleAndRest.split('\n');
	const title = titleLines[0].trim() || calloutType.charAt(0).toUpperCase() + calloutType.slice(1);

	// Mark this blockquote as a callout by injecting data into the token
	// We use a custom property that our renderer will detect
	(bq as unknown as Record<string, unknown>)._callout = {
		type: calloutType,
		title,
		foldable: foldChar !== '',
		defaultOpen: foldChar !== '-'
	};

	// Remove the callout header from the paragraph text
	// Keep only remaining lines as content
	if (titleLines.length > 1) {
		const remaining = titleLines.slice(1).join('\n');
		para.text = remaining;
		para.raw = remaining;
		// Re-lex the inline tokens for the remaining text
		if (para.tokens) {
			para.tokens = [];
		}
	} else {
		// No remaining text in first paragraph - remove it
		bq.tokens.shift();
	}
}

/**
 * Walk tokens to detect and enhance task list items with custom checkboxes.
 * Obsidian supports custom checkbox statuses beyond [ ] and [x].
 */
function walkTokensForTaskLists(token: Token): void {
	if (token.type !== 'list_item') return;

	const li = token as Tokens.ListItem;
	if (!li.task) return;

	// Get the raw text to detect custom checkbox status
	const rawText = li.raw || li.text || '';

	// Match custom checkbox patterns: [x], [/], [-], [>], etc.
	const customCheckMatch = rawText.match(/^\[(.)\]\s/);

	if (customCheckMatch) {
		const status = customCheckMatch[1];
		// Mark this list item with custom task status
		(li as unknown as Record<string, unknown>)._taskStatus = status;
	}
}

// ---------------------------------------------------------------------------
// Configure marked instance
// ---------------------------------------------------------------------------

const marked = new Marked(
	markedHighlight({
		langPrefix: 'hljs language-',
		highlight(code, lang) {
			// Mermaid: pass through as a special div instead of highlighting
			if (lang === 'mermaid') {
				return code;
			}
			const language = hljs.getLanguage(lang) ? lang : 'plaintext';
			return hljs.highlight(code, { language }).value;
		}
	}),
	markedFootnote()
);

// Add inline extensions
marked.use({
	extensions: [highlightExtension, wikilinkExtension, tagExtension, embedExtension]
});

// Add walkTokens for callout and task list detection
marked.use({
	walkTokens(token: Token) {
		walkTokensForCallouts(token);
		walkTokensForTaskLists(token);
	}
});

// Custom renderer for callouts, mermaid, code blocks, and task lists
marked.use({
	renderer: {
		blockquote(this: unknown, token: Tokens.Blockquote) {
			const callout = (token as unknown as Record<string, unknown>)._callout as
				| { type: string; title: string; foldable: boolean; defaultOpen: boolean }
				| undefined;

			if (!callout) {
				// Regular blockquote - use default rendering
				// Render child tokens to HTML
				const body = this && typeof (this as { parser?: { parse: (tokens: Token[]) => string } }).parser?.parse === 'function'
					? (this as { parser: { parse: (tokens: Token[]) => string } }).parser.parse(token.tokens)
					: token.text || '';
				return `<blockquote>\n${body}</blockquote>\n`;
			}

			const meta = CALLOUT_TYPES[callout.type] || CALLOUT_TYPES['note'];
			const colorClass = `callout-${meta.color}`;
			const typeClass = `callout-${callout.type}`;

			// Render content tokens
			const contentHtml = this && typeof (this as { parser?: { parse: (tokens: Token[]) => string } }).parser?.parse === 'function'
				? (this as { parser: { parse: (tokens: Token[]) => string } }).parser.parse(token.tokens)
				: token.text || '';

			const titleHtml = `<div class="callout-title"><span class="callout-icon">${meta.icon}</span><span class="callout-title-text">${escapeHtml(callout.title)}</span>${callout.foldable ? '<span class="callout-fold-icon"></span>' : ''}</div>`;
			const contentWrapper = contentHtml.trim()
				? `<div class="callout-content">${contentHtml}</div>`
				: '';

			if (callout.foldable) {
				const openAttr = callout.defaultOpen ? ' open' : '';
				return `<details class="callout ${colorClass} ${typeClass}"${openAttr}><summary class="callout-header">${titleHtml}</summary>${contentWrapper}</details>\n`;
			}

			return `<div class="callout ${colorClass} ${typeClass}"><div class="callout-header">${titleHtml}</div>${contentWrapper}</div>\n`;
		},

		code(token: Tokens.Code) {
			if (token.lang === 'mermaid') {
				return `<div class="mermaid">${token.text}</div>\n`;
			}

			// Code block with copy button and language label
			const lang = token.lang || 'text';
			const langDisplay = lang.charAt(0).toUpperCase() + lang.slice(1);
			const langClass = ` class="hljs language-${lang}"`;

			// Code content - already highlighted by marked-highlight
			const codeContent = token.text;

			return `<div class="code-block-container">
				<div class="code-block-header">
					<span class="code-lang">${langDisplay}</span>
					<button class="code-copy-btn">Copy</button>
				</div>
				<pre><code${langClass}>${codeContent}</code></pre>
			</div>\n`;
		},

		listitem(this: unknown, token: Tokens.ListItem) {
			const taskStatus = (token as unknown as Record<string, unknown>)._taskStatus as string | undefined;

			// Render child tokens
			const body = this && typeof (this as { parser?: { parse: (tokens: Token[]) => string } }).parser?.parse === 'function'
				? (this as { parser: { parse: (tokens: Token[]) => string } }).parser.parse(token.tokens)
				: token.text || '';

			if (token.task) {
				// Task list item - replace default checkbox with custom one
				const checked = token.checked ? 'checked' : '';
				const status = taskStatus || (token.checked ? 'x' : ' ');

				// Remove the default checkbox if present in body
				const cleanBody = body.replace(/^<input[^>]*>\s*/, '');

				const safeStatus = /^[a-zA-Z0-9 \/?!*<>ilbS"\-x]$/.test(status) ? status.replace(/"/g, '&quot;') : ' ';
			return `<li class="task-list-item"><input type="checkbox" disabled ${checked} data-task="${safeStatus}"> ${cleanBody}</li>\n`;
			}

			// Regular list item
			return `<li>${body}</li>\n`;
		}
	}
});

// Configure marked options for GFM support
marked.setOptions({
	gfm: true,
	breaks: false,
	pedantic: false
});

// ---------------------------------------------------------------------------
// DOMPurify configuration
// ---------------------------------------------------------------------------

const SANITIZE_CONFIG = {
	ALLOWED_TAGS: [
		// Standard HTML
		'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
		'p', 'a', 'ul', 'ol', 'li',
		'blockquote', 'code', 'pre',
		'strong', 'em', 'del',
		'table', 'thead', 'tbody', 'tr', 'th', 'td',
		'br', 'hr', 'img', 'span', 'div',
		// Highlights
		'mark',
		// Callouts (foldable)
		'details', 'summary',
		// Footnotes
		'section', 'sup', 'sub',
		// Task lists and code blocks
		'input', 'button',
		// KaTeX math elements
		'math', 'semantics', 'mrow', 'mi', 'mo', 'mn', 'ms',
		'msup', 'msub', 'mfrac', 'mover', 'munder', 'munderover',
		'mtable', 'mtr', 'mtd', 'mtext', 'mspace', 'mpadded',
		'menclose', 'mglyph', 'msqrt', 'mroot', 'mstyle',
		'annotation', 'annotation-xml',
		// SVG (for mermaid and KaTeX)
		'svg', 'g', 'path', 'line', 'rect', 'circle', 'ellipse',
		'polygon', 'polyline', 'text', 'tspan',
		'defs', 'clipPath', 'use', 'symbol', 'marker',
		'foreignObject', 'image'
	],
	ALLOWED_ATTR: [
		'href', 'title', 'src', 'alt', 'class', 'id',
		'target', 'rel',
		// Callouts
		'open',
		// Task lists and code blocks
		'type', 'disabled', 'checked',
		// KaTeX & SVG
		'style', 'aria-hidden', 'role',
		'viewBox', 'xmlns', 'xmlns:xlink',
		'd', 'fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin',
		'width', 'height', 'x', 'y', 'cx', 'cy', 'r', 'rx', 'ry',
		'transform', 'opacity', 'clip-path', 'clip-rule', 'fill-rule',
		'font-size', 'font-family', 'text-anchor', 'dominant-baseline',
		'dx', 'dy', 'x1', 'y1', 'x2', 'y2',
		'points', 'marker-end', 'marker-start',
		'xlink:href',
		// Data attributes for mermaid, task lists, code blocks
		'data-*'
	],
	ALLOW_DATA_ATTR: true,
	// Allow KaTeX style attributes but limit to safe properties
	FORBID_TAGS: [] as string[],
	FORBID_ATTR: [] as string[]
};

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Context for rendering markdown.
 * Used to resolve embeds and links.
 */
export interface RenderContext {
	/** Web share slug for asset URLs */
	slug?: string;
	/** Folder items for resolving note embeds */
	folderItems?: Array<{ path: string; name: string; type: string; content?: string }>;
}

/** Module-level storage for render context (accessed by embed extension renderer) */
let _renderContext: RenderContext = {};

/**
 * Parse and render markdown to HTML.
 * Supports Obsidian-flavored markdown features.
 * Sanitizes HTML output to prevent XSS.
 */
export async function renderMarkdown(markdown: string, context?: RenderContext): Promise<string> {
	// Reset math store for this render
	resetMathStore();

	// Store context for embed extension renderer
	_renderContext = context || {};

	// Step 1: Preprocess (strip frontmatter, comments, protect math)
	const preprocessed = preprocessMarkdown(markdown);

	// Step 2: Parse with marked (extensions handle highlights, wikilinks, callouts, footnotes, mermaid)
	const rawHtml = await marked.parse(preprocessed);

	// Step 3: Restore math placeholders with KaTeX-rendered HTML
	const withMath = restoreMath(rawHtml);

	// Step 4: Sanitize
	const sanitizedHtml = DOMPurify.sanitize(withMath, SANITIZE_CONFIG);

	// Clear context after rendering
	_renderContext = {};

	return sanitizedHtml;
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
