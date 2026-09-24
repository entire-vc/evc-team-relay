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
import DOMPurify from 'isomorphic-dompurify';
import type { HLJSApi, LanguageFn } from 'highlight.js';
import { env } from '$env/dynamic/public';
import { stripFrontmatter, stripComments } from './markdown-meta';

// Kill switch (#cee667d8): PUBLIC_LAZY_RENDERERS_DISABLED=true reverts to the
// pre-fix behaviour — hljs core + katex are fetched on every render, whether
// or not the document actually has code/math, same as when they were static
// imports. $env/dynamic/public (not $env/static/public) so this is a runtime
// container-restart toggle, not something that needs a rebuild+redeploy to
// flip — the point of a kill switch is not needing the slow path to turn it
// off.
const LAZY_RENDERERS_DISABLED = env.PUBLIC_LAZY_RENDERERS_DISABLED === 'true';

// Title/description/reading-time extraction lives in ./markdown-meta (no
// marked/hljs/katex/dompurify deps) — re-exported here only so existing
// server-side and test imports of `$lib/markdown` keep working. Client route
// components must import them from `$lib/markdown-meta` directly, not via
// this re-export, or Rollup can't split this module's heavy deps out of
// their chunk (see the module-splitting note at the top of markdown-meta.ts).
export { extractTitle, extractDescription, estimateReadingTime } from './markdown-meta';

// highlight.js and katex are NOT imported statically here on purpose. Both
// are loaded via dynamic import(), and only when the document actually
// contains something that needs them (a fenced code block / a math
// expression) — see highlightCode()/restoreMath() below. This module is
// imported by client components (MarkdownViewer.svelte /
// EditableMarkdownViewer.svelte) as well as server load()s, so a static
// import here shipped hljs's full language bundle + katex to every visitor
// of every published page, even ones with neither code nor math (#cee667d8).

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

type MathStore = Map<string, { expression: string; displayMode: boolean }>;

/**
 * Creates a placeholder-writer scoped to a single renderMarkdown() call.
 * MUST be call-scoped, not module state: renderMarkdown() now runs inside
 * SvelteKit's server load() (TR-37), where one Node process serves many
 * requests concurrently — a shared/module-level store gets reset and
 * overwritten mid-flight by a *different* request's render, cross-
 * contaminating math between two unrelated documents (confirmed via a
 * concurrent-render test: two Promise.all'd renders with different math
 * both resolved to the second call's expression).
 */
function createMathPlaceholderFactory(mathStore: MathStore) {
	let counter = 0;
	return (expression: string, displayMode: boolean): string => {
		const id = `${MATH_PLACEHOLDER_PREFIX}${counter++}${MATH_PLACEHOLDER_SUFFIX}`;
		mathStore.set(id, { expression, displayMode });
		return id;
	};
}

// ---------------------------------------------------------------------------
// Preprocessing pipeline
// ---------------------------------------------------------------------------
// (stripComments/stripFrontmatter now live in ./markdown-meta, imported above)

/**
 * Protect math expressions from marked parsing by replacing them with placeholders.
 * Must be called AFTER stripping comments and frontmatter but BEFORE marked.parse().
 *
 * Order: $$...$$ first (display), then $...$ (inline).
 * Skip anything inside code fences or inline code.
 */
function protectMath(text: string, createPlaceholder: (expression: string, displayMode: boolean) => string): string {
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
		return createPlaceholder(expr.trim(), true);
	});

	// Replace inline math ($...$) - single line only, not empty
	// Negative lookbehind for \ to avoid matching \$
	// Must not start or end with space (Obsidian behavior)
	result = result.replace(/(?<![\\$])\$([^\s$](?:[^$]*[^\s$])?)\$(?!\d)/g, (_match, expr: string) => {
		return createPlaceholder(expr, false);
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
function preprocessMarkdown(raw: string, createPlaceholder: (expression: string, displayMode: boolean) => string): string {
	let text = raw;
	text = stripFrontmatter(text);
	text = stripComments(text);
	text = protectMath(text, createPlaceholder);
	return text;
}

// ---------------------------------------------------------------------------
// Post-processing: restore math placeholders with KaTeX HTML
// ---------------------------------------------------------------------------

/**
 * Replace math placeholders with rendered KaTeX HTML.
 * No-ops (and never imports katex) when the document has no math at all.
 */
async function restoreMath(html: string, mathStore: MathStore): Promise<string> {
	if (mathStore.size === 0) return html;
	const katex = (await import('katex')).default;
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
 *
 * Built fresh per renderMarkdown() call (via createEmbedExtension), closing
 * over that call's own `context` — NOT module-level state. renderMarkdown()
 * now runs inside SvelteKit's server load() (TR-37), where one Node process
 * serves many requests concurrently; a shared module-level render context
 * would get overwritten mid-render by a different request for a different
 * share, leaking one document's embed/slug resolution into another's output.
 */
function createEmbedExtension(context: RenderContext) {
	return {
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
				if (context.slug) {
					const styleAttr = safeSize ? ` style="max-width: ${safeSize}px"` : '';
					return `<img class="obsidian-embed-image" src="/${context.slug}/_assets/${safeTarget}" alt="${safeTarget}"${styleAttr} />`;
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
			if (context.folderItems) {
				const noteItem = context.folderItems.find(
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
}

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
// Lazy syntax highlighting (highlight.js/lib/core + one grammar per language)
// ---------------------------------------------------------------------------

/**
 * Fence-label aliases whose highlight.js module filename differs from the
 * label. GENERATED from the installed package's own metadata (each
 * language's `aliases` field) — do not hand-edit; regenerate on hljs
 * version bumps via: for each of the ~193 files in
 * node_modules/highlight.js/lib/languages/*.js (excluding the deprecated
 * `.js.js` re-export shims), `hljs.getLanguage(name).aliases`.
 */
const HLJS_LANGUAGE_ALIASES: Record<string, string> = {
	ado: "stata",
	adoc: "asciidoc",
	ahk: "autohotkey",
	apacheconf: "apache",
	arm: "armasm",
	as: "actionscript",
	asc: "angelscript",
	atom: "xml",
	bat: "dos",
	batch: "dos",
	bf: "brainfuck",
	bind: "dns",
	"c#": "csharp",
	"c++": "cpp",
	capnp: "capnproto",
	cc: "cpp",
	cjs: "javascript",
	clj: "clojure",
	cls: "cos",
	"cmake.in": "cmake",
	cmd: "dos",
	coffee: "coffeescript",
	console: "shell",
	cr: "crystal",
	craftcms: "twig",
	crm: "crmsh",
	cs: "csharp",
	cson: "coffeescript",
	cts: "typescript",
	cxx: "cpp",
	dcl: "clean",
	desktop: "freedesktop",
	dfm: "delphi",
	do: "stata",
	docker: "dockerfile",
	dpr: "delphi",
	dst: "dust",
	edn: "clojure",
	erl: "erlang",
	ex: "elixir",
	exs: "elixir",
	"f#": "fsharp",
	f90: "fortran",
	f95: "fortran",
	feature: "gherkin",
	fs: "fsharp",
	gemspec: "ruby",
	gms: "gams",
	golang: "go",
	gql: "graphql",
	graph: "roboconf",
	gss: "gauss",
	gyp: "python",
	h: "c",
	"h++": "cpp",
	hbs: "handlebars",
	hh: "cpp",
	hpp: "cpp",
	hs: "haskell",
	html: "xml",
	"html.handlebars": "handlebars",
	"html.hbs": "handlebars",
	htmlbars: "handlebars",
	https: "http",
	hx: "haxe",
	hxx: "cpp",
	hylang: "hy",
	i7: "inform7",
	iced: "coffeescript",
	icl: "clean",
	ino: "arduino",
	instances: "roboconf",
	ipython: "python",
	irb: "ruby",
	jinja: "django",
	jldoctest: "julia-repl",
	js: "javascript",
	json5: "json",
	jsonc: "json",
	jsp: "java",
	jsx: "javascript",
	k: "q",
	kdb: "q",
	kt: "kotlin",
	ktm: "kotlin",
	kts: "kotlin",
	ktx: "kotlin",
	lassoscript: "lasso",
	ls: "livescript",
	m: "mercury",
	mak: "makefile",
	make: "makefile",
	md: "markdown",
	mikrotik: "routeros",
	mips: "mipsasm",
	mjs: "javascript",
	mk: "makefile",
	mkd: "markdown",
	mkdown: "markdown",
	ml: "sml",
	mm: "objectivec",
	mma: "mathematica",
	moo: "mercury",
	moon: "moonscript",
	mts: "typescript",
	nc: "gcode",
	nginxconf: "nginx",
	nixos: "nix",
	nt: "nestedtext",
	"obj-c": "objectivec",
	"obj-c++": "objectivec",
	objc: "objectivec",
	"objective-c++": "objectivec",
	osascript: "applescript",
	p21: "step21",
	pas: "delphi",
	pascal: "delphi",
	patch: "diff",
	pb: "purebasic",
	pbi: "purebasic",
	pcmk: "crmsh",
	pde: "processing",
	"pf.conf": "pf",
	pl: "perl",
	plist: "xml",
	pluto: "lua",
	pm: "perl",
	podspec: "ruby",
	postgres: "pgsql",
	postgresql: "pgsql",
	pp: "puppet",
	proto: "protobuf",
	ps: "powershell",
	ps1: "powershell",
	pwsh: "powershell",
	py: "python",
	pycon: "python-repl",
	qt: "qml",
	rb: "ruby",
	re: "reasonml",
	rs: "rust",
	rss: "xml",
	scad: "openscad",
	sci: "scilab",
	scm: "scheme",
	sh: "bash",
	shellsession: "shell",
	st: "smalltalk",
	stanfuncs: "stan",
	step: "step21",
	stp: "step21",
	styl: "stylus",
	sv: "verilog",
	svg: "xml",
	svh: "verilog",
	systemd: "freedesktop",
	tao: "xl",
	tex: "latex",
	text: "plaintext",
	thor: "ruby",
	tk: "tcl",
	toml: "ini",
	ts: "typescript",
	tsx: "typescript",
	txt: "plaintext",
	v: "verilog",
	vb: "vbnet",
	vbs: "vbscript",
	"wildfly-cli": "jboss-cli",
	wl: "mathematica",
	wsf: "xml",
	"x++": "axapta",
	xhtml: "xml",
	xjb: "xml",
	xls: "excel",
	xlsx: "excel",
	xpath: "xquery",
	xq: "xquery",
	xqm: "xquery",
	xsd: "xml",
	xsl: "xml",
	yml: "yaml",
	zep: "zephir",
	zone: "dns",
	zsh: "bash",
};

/** Every real highlight.js language id matches this — also rejects a hostile fence label before it can be used as a lookup key. */
const SAFE_HLJS_LANG_ID = /^[a-z0-9+#.-]+$/;

let hljsCorePromise: Promise<HLJSApi> | null = null;
function loadHljsCore(): Promise<HLJSApi> {
	if (!hljsCorePromise) {
		hljsCorePromise = import('highlight.js/lib/core').then((m) => m.default);
	}
	return hljsCorePromise;
}

/**
 * One literal (non-templated) dynamic import() per language, generated from
 * node_modules/highlight.js/lib/languages/*.js. MUST stay literal specifiers:
 * Vite/Rollup only resolves a bare-package dynamic import when it can see the
 * exact string at build time. A template literal into a computed path (e.g.
 * `import(`highlight.js/lib/languages/${lang}.js`)`) compiles and passes
 * under Node (vitest, SSR — Node resolves bare specifiers at runtime
 * regardless), but ships unresolved in the browser bundle and throws
 * "Failed to resolve module specifier" the moment a client-side render path
 * actually calls it — confirmed live against a real page (#cee667d8).
 */
const HLJS_LANGUAGE_LOADERS: Record<string, () => Promise<{ default: LanguageFn }>> = {
	"1c": () => import("highlight.js/lib/languages/1c"),
	abnf: () => import("highlight.js/lib/languages/abnf"),
	accesslog: () => import("highlight.js/lib/languages/accesslog"),
	actionscript: () => import("highlight.js/lib/languages/actionscript"),
	ada: () => import("highlight.js/lib/languages/ada"),
	angelscript: () => import("highlight.js/lib/languages/angelscript"),
	apache: () => import("highlight.js/lib/languages/apache"),
	applescript: () => import("highlight.js/lib/languages/applescript"),
	arcade: () => import("highlight.js/lib/languages/arcade"),
	arduino: () => import("highlight.js/lib/languages/arduino"),
	armasm: () => import("highlight.js/lib/languages/armasm"),
	asciidoc: () => import("highlight.js/lib/languages/asciidoc"),
	aspectj: () => import("highlight.js/lib/languages/aspectj"),
	autohotkey: () => import("highlight.js/lib/languages/autohotkey"),
	autoit: () => import("highlight.js/lib/languages/autoit"),
	avrasm: () => import("highlight.js/lib/languages/avrasm"),
	awk: () => import("highlight.js/lib/languages/awk"),
	axapta: () => import("highlight.js/lib/languages/axapta"),
	bash: () => import("highlight.js/lib/languages/bash"),
	basic: () => import("highlight.js/lib/languages/basic"),
	bnf: () => import("highlight.js/lib/languages/bnf"),
	brainfuck: () => import("highlight.js/lib/languages/brainfuck"),
	c: () => import("highlight.js/lib/languages/c"),
	cal: () => import("highlight.js/lib/languages/cal"),
	capnproto: () => import("highlight.js/lib/languages/capnproto"),
	ceylon: () => import("highlight.js/lib/languages/ceylon"),
	clean: () => import("highlight.js/lib/languages/clean"),
	clojure: () => import("highlight.js/lib/languages/clojure"),
	"clojure-repl": () => import("highlight.js/lib/languages/clojure-repl"),
	cmake: () => import("highlight.js/lib/languages/cmake"),
	coffeescript: () => import("highlight.js/lib/languages/coffeescript"),
	coq: () => import("highlight.js/lib/languages/coq"),
	cos: () => import("highlight.js/lib/languages/cos"),
	cpp: () => import("highlight.js/lib/languages/cpp"),
	crmsh: () => import("highlight.js/lib/languages/crmsh"),
	crystal: () => import("highlight.js/lib/languages/crystal"),
	csharp: () => import("highlight.js/lib/languages/csharp"),
	csp: () => import("highlight.js/lib/languages/csp"),
	css: () => import("highlight.js/lib/languages/css"),
	d: () => import("highlight.js/lib/languages/d"),
	dart: () => import("highlight.js/lib/languages/dart"),
	delphi: () => import("highlight.js/lib/languages/delphi"),
	diff: () => import("highlight.js/lib/languages/diff"),
	django: () => import("highlight.js/lib/languages/django"),
	dns: () => import("highlight.js/lib/languages/dns"),
	dockerfile: () => import("highlight.js/lib/languages/dockerfile"),
	dos: () => import("highlight.js/lib/languages/dos"),
	dsconfig: () => import("highlight.js/lib/languages/dsconfig"),
	dts: () => import("highlight.js/lib/languages/dts"),
	dust: () => import("highlight.js/lib/languages/dust"),
	ebnf: () => import("highlight.js/lib/languages/ebnf"),
	elixir: () => import("highlight.js/lib/languages/elixir"),
	elm: () => import("highlight.js/lib/languages/elm"),
	erb: () => import("highlight.js/lib/languages/erb"),
	erlang: () => import("highlight.js/lib/languages/erlang"),
	"erlang-repl": () => import("highlight.js/lib/languages/erlang-repl"),
	excel: () => import("highlight.js/lib/languages/excel"),
	fix: () => import("highlight.js/lib/languages/fix"),
	flix: () => import("highlight.js/lib/languages/flix"),
	fortran: () => import("highlight.js/lib/languages/fortran"),
	freedesktop: () => import("highlight.js/lib/languages/freedesktop"),
	fsharp: () => import("highlight.js/lib/languages/fsharp"),
	gams: () => import("highlight.js/lib/languages/gams"),
	gauss: () => import("highlight.js/lib/languages/gauss"),
	gcode: () => import("highlight.js/lib/languages/gcode"),
	gherkin: () => import("highlight.js/lib/languages/gherkin"),
	glsl: () => import("highlight.js/lib/languages/glsl"),
	gml: () => import("highlight.js/lib/languages/gml"),
	go: () => import("highlight.js/lib/languages/go"),
	golo: () => import("highlight.js/lib/languages/golo"),
	gradle: () => import("highlight.js/lib/languages/gradle"),
	graphql: () => import("highlight.js/lib/languages/graphql"),
	groovy: () => import("highlight.js/lib/languages/groovy"),
	haml: () => import("highlight.js/lib/languages/haml"),
	handlebars: () => import("highlight.js/lib/languages/handlebars"),
	haskell: () => import("highlight.js/lib/languages/haskell"),
	haxe: () => import("highlight.js/lib/languages/haxe"),
	hsp: () => import("highlight.js/lib/languages/hsp"),
	http: () => import("highlight.js/lib/languages/http"),
	hy: () => import("highlight.js/lib/languages/hy"),
	inform7: () => import("highlight.js/lib/languages/inform7"),
	ini: () => import("highlight.js/lib/languages/ini"),
	irpf90: () => import("highlight.js/lib/languages/irpf90"),
	isbl: () => import("highlight.js/lib/languages/isbl"),
	java: () => import("highlight.js/lib/languages/java"),
	javascript: () => import("highlight.js/lib/languages/javascript"),
	"jboss-cli": () => import("highlight.js/lib/languages/jboss-cli"),
	json: () => import("highlight.js/lib/languages/json"),
	julia: () => import("highlight.js/lib/languages/julia"),
	"julia-repl": () => import("highlight.js/lib/languages/julia-repl"),
	kotlin: () => import("highlight.js/lib/languages/kotlin"),
	lasso: () => import("highlight.js/lib/languages/lasso"),
	latex: () => import("highlight.js/lib/languages/latex"),
	ldif: () => import("highlight.js/lib/languages/ldif"),
	leaf: () => import("highlight.js/lib/languages/leaf"),
	less: () => import("highlight.js/lib/languages/less"),
	lisp: () => import("highlight.js/lib/languages/lisp"),
	livecodeserver: () => import("highlight.js/lib/languages/livecodeserver"),
	livescript: () => import("highlight.js/lib/languages/livescript"),
	llvm: () => import("highlight.js/lib/languages/llvm"),
	lsl: () => import("highlight.js/lib/languages/lsl"),
	lua: () => import("highlight.js/lib/languages/lua"),
	makefile: () => import("highlight.js/lib/languages/makefile"),
	markdown: () => import("highlight.js/lib/languages/markdown"),
	mathematica: () => import("highlight.js/lib/languages/mathematica"),
	matlab: () => import("highlight.js/lib/languages/matlab"),
	maxima: () => import("highlight.js/lib/languages/maxima"),
	mel: () => import("highlight.js/lib/languages/mel"),
	mercury: () => import("highlight.js/lib/languages/mercury"),
	mipsasm: () => import("highlight.js/lib/languages/mipsasm"),
	mizar: () => import("highlight.js/lib/languages/mizar"),
	mojolicious: () => import("highlight.js/lib/languages/mojolicious"),
	monkey: () => import("highlight.js/lib/languages/monkey"),
	moonscript: () => import("highlight.js/lib/languages/moonscript"),
	n1ql: () => import("highlight.js/lib/languages/n1ql"),
	nestedtext: () => import("highlight.js/lib/languages/nestedtext"),
	nginx: () => import("highlight.js/lib/languages/nginx"),
	nim: () => import("highlight.js/lib/languages/nim"),
	nix: () => import("highlight.js/lib/languages/nix"),
	"node-repl": () => import("highlight.js/lib/languages/node-repl"),
	nsis: () => import("highlight.js/lib/languages/nsis"),
	objectivec: () => import("highlight.js/lib/languages/objectivec"),
	ocaml: () => import("highlight.js/lib/languages/ocaml"),
	openscad: () => import("highlight.js/lib/languages/openscad"),
	oxygene: () => import("highlight.js/lib/languages/oxygene"),
	parser3: () => import("highlight.js/lib/languages/parser3"),
	perl: () => import("highlight.js/lib/languages/perl"),
	pf: () => import("highlight.js/lib/languages/pf"),
	pgsql: () => import("highlight.js/lib/languages/pgsql"),
	php: () => import("highlight.js/lib/languages/php"),
	"php-template": () => import("highlight.js/lib/languages/php-template"),
	plaintext: () => import("highlight.js/lib/languages/plaintext"),
	pony: () => import("highlight.js/lib/languages/pony"),
	powershell: () => import("highlight.js/lib/languages/powershell"),
	processing: () => import("highlight.js/lib/languages/processing"),
	profile: () => import("highlight.js/lib/languages/profile"),
	prolog: () => import("highlight.js/lib/languages/prolog"),
	properties: () => import("highlight.js/lib/languages/properties"),
	protobuf: () => import("highlight.js/lib/languages/protobuf"),
	puppet: () => import("highlight.js/lib/languages/puppet"),
	purebasic: () => import("highlight.js/lib/languages/purebasic"),
	python: () => import("highlight.js/lib/languages/python"),
	"python-repl": () => import("highlight.js/lib/languages/python-repl"),
	q: () => import("highlight.js/lib/languages/q"),
	qml: () => import("highlight.js/lib/languages/qml"),
	r: () => import("highlight.js/lib/languages/r"),
	reasonml: () => import("highlight.js/lib/languages/reasonml"),
	rib: () => import("highlight.js/lib/languages/rib"),
	roboconf: () => import("highlight.js/lib/languages/roboconf"),
	routeros: () => import("highlight.js/lib/languages/routeros"),
	rsl: () => import("highlight.js/lib/languages/rsl"),
	ruby: () => import("highlight.js/lib/languages/ruby"),
	ruleslanguage: () => import("highlight.js/lib/languages/ruleslanguage"),
	rust: () => import("highlight.js/lib/languages/rust"),
	sas: () => import("highlight.js/lib/languages/sas"),
	scala: () => import("highlight.js/lib/languages/scala"),
	scheme: () => import("highlight.js/lib/languages/scheme"),
	scilab: () => import("highlight.js/lib/languages/scilab"),
	scss: () => import("highlight.js/lib/languages/scss"),
	shell: () => import("highlight.js/lib/languages/shell"),
	smali: () => import("highlight.js/lib/languages/smali"),
	smalltalk: () => import("highlight.js/lib/languages/smalltalk"),
	sml: () => import("highlight.js/lib/languages/sml"),
	sqf: () => import("highlight.js/lib/languages/sqf"),
	sql: () => import("highlight.js/lib/languages/sql"),
	stan: () => import("highlight.js/lib/languages/stan"),
	stata: () => import("highlight.js/lib/languages/stata"),
	step21: () => import("highlight.js/lib/languages/step21"),
	stylus: () => import("highlight.js/lib/languages/stylus"),
	subunit: () => import("highlight.js/lib/languages/subunit"),
	swift: () => import("highlight.js/lib/languages/swift"),
	taggerscript: () => import("highlight.js/lib/languages/taggerscript"),
	tap: () => import("highlight.js/lib/languages/tap"),
	tcl: () => import("highlight.js/lib/languages/tcl"),
	thrift: () => import("highlight.js/lib/languages/thrift"),
	tp: () => import("highlight.js/lib/languages/tp"),
	twig: () => import("highlight.js/lib/languages/twig"),
	typescript: () => import("highlight.js/lib/languages/typescript"),
	vala: () => import("highlight.js/lib/languages/vala"),
	vbnet: () => import("highlight.js/lib/languages/vbnet"),
	vbscript: () => import("highlight.js/lib/languages/vbscript"),
	"vbscript-html": () => import("highlight.js/lib/languages/vbscript-html"),
	verilog: () => import("highlight.js/lib/languages/verilog"),
	vhdl: () => import("highlight.js/lib/languages/vhdl"),
	vim: () => import("highlight.js/lib/languages/vim"),
	wasm: () => import("highlight.js/lib/languages/wasm"),
	wren: () => import("highlight.js/lib/languages/wren"),
	x86asm: () => import("highlight.js/lib/languages/x86asm"),
	xl: () => import("highlight.js/lib/languages/xl"),
	xml: () => import("highlight.js/lib/languages/xml"),
	xquery: () => import("highlight.js/lib/languages/xquery"),
	yaml: () => import("highlight.js/lib/languages/yaml"),
	zephir: () => import("highlight.js/lib/languages/zephir"),
};

/** Languages we've already tried to register — caches the PROMISE, not a boolean, so concurrent fenced blocks in the same document (marked awaits all code-token callbacks via one Promise.all) await the same in-flight registration instead of racing and losing. */
const hljsLanguageAttempts = new Map<string, Promise<string | null>>();

function ensureHljsLanguage(hljs: HLJSApi, requested: string): Promise<string | null> {
	const canonical = HLJS_LANGUAGE_ALIASES[requested] ?? requested;
	if (hljs.getLanguage(canonical)) return Promise.resolve(canonical);
	const cached = hljsLanguageAttempts.get(canonical);
	if (cached) return cached;
	const attempt = (async () => {
		if (!SAFE_HLJS_LANG_ID.test(canonical)) return null;
		const loader = HLJS_LANGUAGE_LOADERS[canonical];
		if (!loader) return null;
		try {
			const mod = await loader();
			hljs.registerLanguage(canonical, mod.default);
			return canonical;
		} catch {
			return null;
		}
	})();
	hljsLanguageAttempts.set(canonical, attempt);
	return attempt;
}

/**
 * Highlight one fenced code block's contents. Loads highlight.js core and
 * the requested grammar on demand — a document with no fenced code (or only
 * unlabelled fences) never imports highlight.js at all, UNLESS the kill
 * switch is set, in which case every block (including unlabelled ones) goes
 * through hljs's 'plaintext' grammar, matching the pre-#cee667d8 behaviour.
 * Unknown/unsupported languages degrade to plain escaped text either way.
 */
async function highlightCode(code: string, lang: string): Promise<string> {
	if (!lang && !LAZY_RENDERERS_DISABLED) return escapeHtml(code);
	const hljs = await loadHljsCore();
	const resolved = await ensureHljsLanguage(hljs, (lang || 'plaintext').toLowerCase());
	if (!resolved) return escapeHtml(code);
	return hljs.highlight(code, { language: resolved }).value;
}

// ---------------------------------------------------------------------------
// Configure marked instance
// ---------------------------------------------------------------------------
//
// Built fresh per renderMarkdown() call (see buildMarkedInstance below), NOT
// as a shared module-level singleton — renderMarkdown() runs inside
// SvelteKit's server load() (TR-37), and marked.use() mutates the instance
// it's called on, so a shared instance reconfigured per-request would race
// exactly like the module-level render context did (see createEmbedExtension
// above). highlightExtension/wikilinkExtension/tagExtension/walkTokens/the
// custom renderer below are all stateless (no per-call context), so they're
// safe to reuse as-is across instances — only the Marked instance itself and
// the context-dependent embedExtension are rebuilt per call.

const customRenderer = {
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
	};

/** Builds a fresh Marked instance scoped to a single renderMarkdown() call. */
function buildMarkedInstance(context: RenderContext): Marked {
	const instance = new Marked(
		markedHighlight({
			async: true,
			langPrefix: 'hljs language-',
			async highlight(code, lang) {
				// Mermaid: pass through as a special div instead of highlighting
				// (mermaid itself is loaded client-side, separately — see MarkdownViewer.svelte)
				if (lang === 'mermaid') {
					return code;
				}
				return highlightCode(code, lang);
			}
		}),
		markedFootnote()
	);

	instance.use({
		extensions: [highlightExtension, wikilinkExtension, tagExtension, createEmbedExtension(context)]
	});

	instance.use({
		walkTokens(token: Token) {
			walkTokensForCallouts(token);
			walkTokensForTaskLists(token);
		}
	});

	instance.use({ renderer: customRenderer });

	instance.setOptions({
		gfm: true,
		breaks: false,
		pedantic: false
	});

	return instance;
}

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

/**
 * Parse and render markdown to HTML.
 * Supports Obsidian-flavored markdown features.
 * Sanitizes HTML output to prevent XSS.
 *
 * Fully self-contained per call (no module-level mutable state) — this runs
 * inside SvelteKit's server load() (TR-37), where one Node process serves
 * many concurrent requests; shared state here would let one document's
 * render bleed into another's.
 */
export async function renderMarkdown(markdown: string, context?: RenderContext): Promise<string> {
	const renderContext = context || {};
	const mathStore: MathStore = new Map();
	const createPlaceholder = createMathPlaceholderFactory(mathStore);

	// Kill switch: warm katex AND hljs core unconditionally, same cost shape
	// as when both were static imports — restoreMath()/highlightCode() below
	// still only USE them if the document actually has math/code, but the
	// fetch happens regardless, even for a document with neither.
	if (LAZY_RENDERERS_DISABLED) {
		void import('katex');
		void loadHljsCore();
	}

	// Step 1: Preprocess (strip frontmatter, comments, protect math)
	const preprocessed = preprocessMarkdown(markdown, createPlaceholder);

	// Step 2: Parse with marked (extensions handle highlights, wikilinks, callouts, footnotes, mermaid)
	const marked = buildMarkedInstance(renderContext);
	const rawHtml = await marked.parse(preprocessed);

	// Step 3: Restore math placeholders with KaTeX-rendered HTML
	const withMath = await restoreMath(rawHtml, mathStore);

	// Step 4: Sanitize
	const sanitizedHtml = DOMPurify.sanitize(withMath, SANITIZE_CONFIG);

	return sanitizedHtml;
}
