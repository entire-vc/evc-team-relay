<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { browser } from '$app/environment';
	import { renderMarkdown } from '$lib/markdown';

	interface Props {
		content: string;
		class?: string;
		slug?: string;
		folderItems?: Array<{ path: string; name: string; type: string; content?: string }>;
	}

	let { content, class: className = '', slug, folderItems }: Props = $props();

	let renderedHtml = $state('');
	let isRendering = $state(true);
	let contentEl: HTMLDivElement | undefined = $state();

	/**
	 * Initialize mermaid diagrams in the rendered content.
	 * Mermaid is loaded dynamically (client-side only) to avoid SSR issues.
	 */
	async function initMermaid() {
		if (!browser || !contentEl) return;

		const mermaidElements = contentEl.querySelectorAll('.mermaid');
		if (mermaidElements.length === 0) return;

		try {
			const mermaid = (await import('mermaid')).default;
			mermaid.initialize({
				startOnLoad: false,
				theme: 'default',
				securityLevel: 'strict',
				fontFamily: 'var(--font-sans)',
				themeVariables: {
					primaryColor: '#818cf8',
					primaryTextColor: '#1e1b4b',
					primaryBorderColor: '#6366f1',
					lineColor: '#6366f1',
					secondaryColor: '#c7d2fe',
					tertiaryColor: '#eef2ff'
				}
			});

			// Render each mermaid element
			let index = 0;
			for (const el of mermaidElements) {
				const code = el.textContent || '';
				if (!code.trim()) continue;

				try {
					const id = `mermaid-${Date.now()}-${index++}`;
					const { svg } = await mermaid.render(id, code);
					el.innerHTML = svg;
					el.classList.add('mermaid-rendered');
				} catch (err) {
					console.warn('[MarkdownViewer] Mermaid render failed for element:', err);
					el.innerHTML = `<div class="mermaid-error"><span class="mermaid-error-icon">!</span> Diagram rendering failed</div><pre class="mermaid-source"><code>${code.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>`;
					el.classList.add('mermaid-error-container');
				}
			}
		} catch (err) {
			console.warn('[MarkdownViewer] Failed to load mermaid library:', err);
		}
	}

	/**
	 * Attach delegated event handlers for copy buttons and wikilink click prevention.
	 * Uses event delegation on the container to avoid per-element listener leaks.
	 */
	function attachDelegatedHandlers() {
		if (!browser || !contentEl) return;

		contentEl.addEventListener('click', async (e) => {
			const target = e.target as HTMLElement;

			// Handle copy button clicks
			if (target.classList.contains('code-copy-btn')) {
				const container = target.closest('.code-block-container');
				if (!container) return;

				const codeEl = container.querySelector('code');
				if (!codeEl) return;

				const codeText = codeEl.textContent || '';

				try {
					await navigator.clipboard.writeText(codeText);
					target.textContent = 'Copied!';
					target.classList.add('copied');
					setTimeout(() => {
						target.textContent = 'Copy';
						target.classList.remove('copied');
					}, 2000);
				} catch (err) {
					console.warn('[MarkdownViewer] Failed to copy code:', err);
					target.textContent = 'Failed';
					setTimeout(() => {
						target.textContent = 'Copy';
					}, 2000);
				}
				return;
			}

			// Prevent navigation for disabled wikilinks
			const wikilink = target.closest('[data-wikilink-disabled]') as HTMLElement | null;
			if (wikilink) {
				e.preventDefault();
			}
		});
	}

	onMount(async () => {
		try {
			renderedHtml = await renderMarkdown(content, { slug, folderItems });
			await tick();
			await initMermaid();
			attachDelegatedHandlers();
		} catch (error) {
			console.error('Failed to render markdown:', error);
			renderedHtml = '<p class="error">Failed to render document</p>';
		} finally {
			isRendering = false;
		}
	});

	// Re-render when content changes
	$effect(() => {
		if (content) {
			isRendering = true;
			renderMarkdown(content, { slug, folderItems })
				.then(async (html) => {
					renderedHtml = html;
					await tick();
					await initMermaid();
					attachDelegatedHandlers();
				})
				.catch((error) => {
					console.error('Failed to render markdown:', error);
					renderedHtml = '<p class="error">Failed to render document</p>';
				})
				.finally(() => {
					isRendering = false;
				});
		}
	});
</script>

<svelte:head>
	<!-- KaTeX CSS for math rendering -->
	<link
		rel="stylesheet"
		href="https://cdn.jsdelivr.net/npm/katex@0.16.28/dist/katex.min.css"
		crossorigin="anonymous"
	/>
	<!-- Highlight.js CSS for code syntax highlighting -->
	<link
		rel="stylesheet"
		href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css"
		media="(prefers-color-scheme: light)"
		crossorigin="anonymous"
	/>
	<link
		rel="stylesheet"
		href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css"
		media="(prefers-color-scheme: dark)"
		crossorigin="anonymous"
	/>
</svelte:head>

{#if isRendering}
	<div class="markdown-loading">
		<div class="skeleton-container">
			<div class="skeleton skeleton-title"></div>
			<div class="skeleton skeleton-line"></div>
			<div class="skeleton skeleton-line"></div>
			<div class="skeleton skeleton-line short"></div>
			<div class="skeleton skeleton-line"></div>
			<div class="skeleton skeleton-line"></div>
		</div>
	</div>
{:else}
	<div class="markdown-content {className}" bind:this={contentEl}>
		{@html renderedHtml}
	</div>
{/if}

<style>
	/* ================================
	   Loading skeleton
	   ================================ */
	.markdown-loading {
		padding: 2rem 0;
	}

	.skeleton-container {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.skeleton {
		background: linear-gradient(90deg, var(--muted) 25%, var(--secondary) 50%, var(--muted) 75%);
		background-size: 200% 100%;
		animation: shimmer 1.5s infinite;
		border-radius: 4px;
		height: 1rem;
	}

	.skeleton-title {
		height: 2rem;
		width: 60%;
		margin-bottom: 0.5rem;
	}

	.skeleton-line {
		width: 100%;
	}

	.skeleton-line.short {
		width: 70%;
	}

	@keyframes shimmer {
		0% {
			background-position: -200% 0;
		}
		100% {
			background-position: 200% 0;
		}
	}

	/* ================================
	   Base markdown content
	   ================================ */
	.markdown-content {
		line-height: 1.6;
		color: var(--foreground);
		animation: fadeIn 0.3s ease-out;
		user-select: text;
		-webkit-user-select: text;
	}

	@keyframes fadeIn {
		from {
			opacity: 0;
		}
		to {
			opacity: 1;
		}
	}

	/* ================================
	   Standard HTML elements
	   ================================ */
	.markdown-content :global(h1) {
		font-size: 2rem;
		margin-top: 1.5rem;
		margin-bottom: 1rem;
		font-weight: 600;
		border-bottom: 1px solid var(--border);
		padding-bottom: 0.3rem;
		color: var(--foreground);
	}

	.markdown-content :global(h2) {
		font-size: 1.5rem;
		margin-top: 1.5rem;
		margin-bottom: 0.75rem;
		font-weight: 600;
		color: var(--foreground);
	}

	.markdown-content :global(h3) {
		font-size: 1.25rem;
		margin-top: 1.25rem;
		margin-bottom: 0.5rem;
		font-weight: 600;
		color: var(--foreground);
	}

	.markdown-content :global(h4),
	.markdown-content :global(h5),
	.markdown-content :global(h6) {
		font-size: 1rem;
		margin-top: 1rem;
		margin-bottom: 0.5rem;
		font-weight: 600;
		color: var(--foreground);
	}

	.markdown-content :global(p) {
		margin-bottom: 1rem;
	}

	.markdown-content :global(a) {
		color: hsl(var(--primary));
		text-decoration: none;
	}

	.markdown-content :global(a:hover) {
		color: hsl(var(--primary-hover));
		text-decoration: underline;
	}

	.markdown-content :global(code) {
		background-color: hsl(var(--code-inline-bg));
		padding: 0.2em 0.4em;
		border-radius: 3px;
		font-family: var(--font-mono);
		font-size: 0.9em;
		color: hsl(var(--code-inline-fg));
	}

	.markdown-content :global(pre) {
		background-color: hsl(var(--code-bg));
		padding: 1rem;
		border-radius: 8px;
		overflow-x: auto;
		margin-bottom: 1rem;
		box-shadow: 0 2px 8px var(--shadow);
		position: relative;
	}

	.markdown-content :global(pre code) {
		background-color: transparent;
		padding: 0;
		color: hsl(var(--code-fg));
		font-size: 0.875rem;
		line-height: 1.5;
	}

	.markdown-content :global(blockquote) {
		border-left: 4px solid hsl(var(--border));
		padding-left: 1rem;
		margin-left: 0;
		color: hsl(var(--muted-foreground));
		font-style: italic;
	}

	.markdown-content :global(ul),
	.markdown-content :global(ol) {
		margin-bottom: 1rem;
		padding-left: 2rem;
	}

	.markdown-content :global(li) {
		margin-bottom: 0.5rem;
	}

	.markdown-content :global(table) {
		border-collapse: collapse;
		width: 100%;
		margin-bottom: 1rem;
	}

	.markdown-content :global(th),
	.markdown-content :global(td) {
		border: 1px solid hsl(var(--border));
		padding: 0.5rem;
		text-align: left;
	}

	.markdown-content :global(th) {
		background-color: hsl(var(--secondary));
		font-weight: 600;
		color: hsl(var(--secondary-foreground));
	}

	.markdown-content :global(img) {
		max-width: 100%;
		height: auto;
	}

	.markdown-content :global(hr) {
		border: none;
		border-top: 1px solid hsl(var(--border));
		margin: 2rem 0;
	}

	.markdown-content :global(.error) {
		color: hsl(var(--destructive));
		padding: 1rem;
		background-color: rgba(239, 68, 68, 0.1);
		border-radius: 5px;
	}

	/* ================================
	   Highlights (==text==)
	   ================================ */
	.markdown-content :global(mark) {
		background-color: rgba(250, 204, 21, 0.4);
		color: inherit;
		padding: 0.1em 0.2em;
		border-radius: 2px;
	}

	:global(.dark) .markdown-content :global(mark) {
		background-color: rgba(250, 204, 21, 0.25);
	}

	/* ================================
	   Wikilinks
	   ================================ */
	.markdown-content :global(.obsidian-wikilink) {
		color: hsl(var(--accent));
		text-decoration: none;
		border-bottom: 1px dashed hsl(var(--accent));
		cursor: default;
		transition: opacity 0.15s;
	}

	.markdown-content :global(.obsidian-wikilink:hover) {
		opacity: 0.8;
		text-decoration: none;
	}

	.markdown-content :global(.obsidian-wikilink-heading) {
		cursor: pointer;
		border-bottom-style: solid;
	}

	/* ================================
	   Obsidian embeds (placeholder)
	   ================================ */
	.markdown-content :global(.obsidian-embed) {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.75rem 1rem;
		margin: 0.5rem 0 1rem;
		background-color: hsl(var(--muted));
		border: 1px dashed hsl(var(--border));
		border-radius: 6px;
		font-size: 0.875rem;
		color: hsl(var(--muted-foreground));
	}

	.markdown-content :global(.obsidian-embed-icon) {
		font-size: 1.1rem;
		flex-shrink: 0;
	}

	.markdown-content :global(.obsidian-embed strong) {
		color: hsl(var(--foreground));
		font-weight: 500;
	}

	/* ================================
	   Callouts
	   ================================ */
	.markdown-content :global(.callout) {
		margin: 1rem 0;
		border-radius: 6px;
		overflow: hidden;
		border: 1px solid;
		font-style: normal;
	}

	.markdown-content :global(.callout-header) {
		padding: 0.5rem 0.75rem;
		font-weight: 500;
	}

	.markdown-content :global(.callout-title) {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.markdown-content :global(.callout-icon) {
		flex-shrink: 0;
		font-size: 1rem;
	}

	.markdown-content :global(.callout-title-text) {
		font-weight: 600;
		font-size: 0.9375rem;
	}

	.markdown-content :global(.callout-fold-icon)::after {
		content: '\25B6';
		font-size: 0.625rem;
		display: inline-block;
		transition: transform 0.2s;
		margin-left: 0.25rem;
	}

	.markdown-content :global(details.callout[open]) :global(.callout-fold-icon)::after {
		transform: rotate(90deg);
	}

	.markdown-content :global(details.callout > summary) {
		cursor: pointer;
		list-style: none;
	}

	.markdown-content :global(details.callout > summary::-webkit-details-marker) {
		display: none;
	}

	.markdown-content :global(details.callout > summary::marker) {
		display: none;
		content: '';
	}

	.markdown-content :global(.callout-content) {
		padding: 0.5rem 0.75rem 0.75rem;
		font-size: 0.9375rem;
		line-height: 1.6;
		border-top: 1px solid;
		border-color: inherit;
	}

	.markdown-content :global(.callout-content > p:last-child) {
		margin-bottom: 0;
	}

	/* Callout color: Blue (note, info, todo) */
	.markdown-content :global(.callout-blue) {
		background-color: rgba(59, 130, 246, 0.08);
		border-color: rgba(59, 130, 246, 0.3);
		color: hsl(var(--foreground));
	}

	.markdown-content :global(.callout-blue) :global(.callout-header) {
		color: #2563eb;
	}

	:global(.dark) .markdown-content :global(.callout-blue) :global(.callout-header) {
		color: #60a5fa;
	}

	/* Callout color: Teal (abstract, summary, tldr) */
	.markdown-content :global(.callout-teal) {
		background-color: rgba(20, 184, 166, 0.08);
		border-color: rgba(20, 184, 166, 0.3);
		color: hsl(var(--foreground));
	}

	.markdown-content :global(.callout-teal) :global(.callout-header) {
		color: #0d9488;
	}

	:global(.dark) .markdown-content :global(.callout-teal) :global(.callout-header) {
		color: #2dd4bf;
	}

	/* Callout color: Cyan (tip, hint, important) */
	.markdown-content :global(.callout-cyan) {
		background-color: rgba(6, 182, 212, 0.08);
		border-color: rgba(6, 182, 212, 0.3);
		color: hsl(var(--foreground));
	}

	.markdown-content :global(.callout-cyan) :global(.callout-header) {
		color: #0891b2;
	}

	:global(.dark) .markdown-content :global(.callout-cyan) :global(.callout-header) {
		color: #22d3ee;
	}

	/* Callout color: Green (success, check, done) */
	.markdown-content :global(.callout-green) {
		background-color: rgba(34, 197, 94, 0.08);
		border-color: rgba(34, 197, 94, 0.3);
		color: hsl(var(--foreground));
	}

	.markdown-content :global(.callout-green) :global(.callout-header) {
		color: #16a34a;
	}

	:global(.dark) .markdown-content :global(.callout-green) :global(.callout-header) {
		color: #4ade80;
	}

	/* Callout color: Yellow (question, help, faq) */
	.markdown-content :global(.callout-yellow) {
		background-color: rgba(234, 179, 8, 0.08);
		border-color: rgba(234, 179, 8, 0.3);
		color: hsl(var(--foreground));
	}

	.markdown-content :global(.callout-yellow) :global(.callout-header) {
		color: #ca8a04;
	}

	:global(.dark) .markdown-content :global(.callout-yellow) :global(.callout-header) {
		color: #facc15;
	}

	/* Callout color: Orange (warning, caution, attention) */
	.markdown-content :global(.callout-orange) {
		background-color: rgba(249, 115, 22, 0.08);
		border-color: rgba(249, 115, 22, 0.3);
		color: hsl(var(--foreground));
	}

	.markdown-content :global(.callout-orange) :global(.callout-header) {
		color: #ea580c;
	}

	:global(.dark) .markdown-content :global(.callout-orange) :global(.callout-header) {
		color: #fb923c;
	}

	/* Callout color: Red (failure, fail, missing, bug) */
	.markdown-content :global(.callout-red) {
		background-color: rgba(239, 68, 68, 0.08);
		border-color: rgba(239, 68, 68, 0.3);
		color: hsl(var(--foreground));
	}

	.markdown-content :global(.callout-red) :global(.callout-header) {
		color: #dc2626;
	}

	:global(.dark) .markdown-content :global(.callout-red) :global(.callout-header) {
		color: #f87171;
	}

	/* Callout color: Red Dark (danger, error) */
	.markdown-content :global(.callout-red-dark) {
		background-color: rgba(185, 28, 28, 0.08);
		border-color: rgba(185, 28, 28, 0.3);
		color: hsl(var(--foreground));
	}

	.markdown-content :global(.callout-red-dark) :global(.callout-header) {
		color: #b91c1c;
	}

	:global(.dark) .markdown-content :global(.callout-red-dark) :global(.callout-header) {
		color: #fca5a5;
	}

	/* Callout color: Purple (example) */
	.markdown-content :global(.callout-purple) {
		background-color: rgba(139, 92, 246, 0.08);
		border-color: rgba(139, 92, 246, 0.3);
		color: hsl(var(--foreground));
	}

	.markdown-content :global(.callout-purple) :global(.callout-header) {
		color: #7c3aed;
	}

	:global(.dark) .markdown-content :global(.callout-purple) :global(.callout-header) {
		color: #a78bfa;
	}

	/* Callout color: Gray (quote, cite) */
	.markdown-content :global(.callout-gray) {
		background-color: rgba(107, 114, 128, 0.08);
		border-color: rgba(107, 114, 128, 0.3);
		color: hsl(var(--foreground));
	}

	.markdown-content :global(.callout-gray) :global(.callout-header) {
		color: #4b5563;
	}

	:global(.dark) .markdown-content :global(.callout-gray) :global(.callout-header) {
		color: #9ca3af;
	}

	/* ================================
	   KaTeX / Math
	   ================================ */
	.markdown-content :global(.katex-display) {
		display: block;
		margin: 1rem 0;
		text-align: center;
		overflow-x: auto;
		overflow-y: hidden;
		padding: 0.5rem 0;
	}

	.markdown-content :global(.katex) {
		font-size: 1.1em;
	}

	.markdown-content :global(.katex-error) {
		color: hsl(var(--destructive));
		font-family: var(--font-mono);
		font-size: 0.9em;
		background-color: rgba(239, 68, 68, 0.08);
		padding: 0.2em 0.4em;
		border-radius: 3px;
	}

	/* ================================
	   Mermaid diagrams
	   ================================ */
	.markdown-content :global(.mermaid) {
		display: flex;
		justify-content: center;
		margin: 1.5rem 0;
		padding: 1rem;
		background-color: hsl(var(--muted));
		border-radius: 8px;
		overflow-x: auto;
	}

	.markdown-content :global(.mermaid-rendered) {
		background-color: transparent;
	}

	.markdown-content :global(.mermaid-rendered svg) {
		max-width: 100%;
		height: auto;
	}

	.markdown-content :global(.mermaid-error) {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		color: hsl(var(--destructive));
		font-size: 0.875rem;
		margin-bottom: 0.5rem;
	}

	.markdown-content :global(.mermaid-error-icon) {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 18px;
		height: 18px;
		background-color: hsl(var(--destructive));
		color: white;
		border-radius: 50%;
		font-weight: bold;
		font-size: 0.75rem;
	}

	.markdown-content :global(.mermaid-error-container) {
		flex-direction: column;
		align-items: flex-start;
	}

	.markdown-content :global(.mermaid-source) {
		font-size: 0.8125rem;
		width: 100%;
	}

	/* ================================
	   Footnotes
	   ================================ */
	.markdown-content :global(section.footnotes) {
		margin-top: 2rem;
		padding-top: 1rem;
		border-top: 1px solid hsl(var(--border));
		font-size: 0.875rem;
		color: hsl(var(--muted-foreground));
	}

	.markdown-content :global(section.footnotes h2) {
		font-size: 0.875rem;
		font-weight: 600;
		margin-top: 0;
		margin-bottom: 0.75rem;
		color: hsl(var(--muted-foreground));
	}

	.markdown-content :global(section.footnotes .sr-only) {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
		border-width: 0;
	}

	.markdown-content :global(section.footnotes ol) {
		padding-left: 1.5rem;
		margin-bottom: 0;
	}

	.markdown-content :global(section.footnotes li) {
		margin-bottom: 0.25rem;
	}

	.markdown-content :global(a[data-backref]) {
		font-size: 0.75rem;
		text-decoration: none;
		margin-left: 0.25rem;
	}

	.markdown-content :global(sup a) {
		color: hsl(var(--primary));
		text-decoration: none;
		font-weight: 500;
	}

	.markdown-content :global(sup a:hover) {
		text-decoration: underline;
	}

	/* ================================
	   Responsive
	   ================================ */
	@media (max-width: 768px) {
		.markdown-content :global(table) {
			display: block;
			overflow-x: auto;
			white-space: nowrap;
		}

		.markdown-content :global(pre) {
			padding: 0.75rem;
		}

		.markdown-content :global(pre code) {
			font-size: 0.8125rem;
		}

		.markdown-content :global(.callout) {
			margin: 0.75rem 0;
		}

		.markdown-content :global(.mermaid) {
			padding: 0.5rem;
		}

		.markdown-content :global(.katex-display) {
			font-size: 0.95em;
		}
	}

	/* ================================
	   Reduce motion for accessibility
	   ================================ */
	@media (prefers-reduced-motion: reduce) {
		.skeleton {
			animation: none;
		}

		.markdown-content {
			animation: none;
		}
	}

	/* ================================
	   Phase B: Tags
	   ================================ */
	.markdown-content :global(.obsidian-tag) {
		display: inline-block;
		padding: 0.125rem 0.5rem;
		font-size: 0.8125rem;
		font-weight: 500;
		background-color: hsl(var(--accent) / 0.15);
		color: hsl(var(--accent-foreground));
		border-radius: 0.375rem;
		border: 1px solid hsl(var(--accent) / 0.3);
		transition: background-color 0.15s, border-color 0.15s;
	}

	:global(.dark) .markdown-content :global(.obsidian-tag) {
		background-color: hsl(var(--accent) / 0.2);
		border-color: hsl(var(--accent) / 0.4);
	}

	/* ================================
	   Phase B: Task Lists with Custom Checkboxes
	   ================================ */
	.markdown-content :global(.task-list-item) {
		list-style: none;
		display: flex;
		align-items: flex-start;
		gap: 0.5rem;
	}

	.markdown-content :global(.task-list-item input[type="checkbox"]) {
		margin-top: 0.25rem;
		cursor: default;
		flex-shrink: 0;
		width: 1rem;
		height: 1rem;
		position: relative;
	}

	/* Hide default checkbox and use custom styling */
	.markdown-content :global(.task-list-item input[type="checkbox"]) {
		appearance: none;
		-webkit-appearance: none;
		border: 2px solid hsl(var(--border));
		border-radius: 3px;
		background-color: hsl(var(--background));
		transition: all 0.15s;
	}

	.markdown-content :global(.task-list-item input[type="checkbox"]::before) {
		content: '';
		display: block;
		position: absolute;
		top: 50%;
		left: 50%;
		transform: translate(-50%, -50%);
		font-size: 0.75rem;
		line-height: 1;
	}

	/* Unchecked [ ] */
	.markdown-content :global(.task-list-item input[data-task=" "]) {
		border-color: hsl(var(--border));
	}

	/* Checked [x] */
	.markdown-content :global(.task-list-item input[data-task="x"])::before {
		content: '✓';
		color: hsl(var(--primary));
		font-weight: bold;
	}

	.markdown-content :global(.task-list-item input[data-task="x"]) {
		border-color: hsl(var(--primary));
		background-color: hsl(var(--primary) / 0.1);
	}

	/* In progress [/] */
	.markdown-content :global(.task-list-item input[data-task="/"]) {
		border-color: #eab308;
		background: linear-gradient(90deg, rgba(234, 179, 8, 0.2) 50%, transparent 50%);
	}

	.markdown-content :global(.task-list-item input[data-task="/"]::before) {
		content: '~';
		color: #eab308;
		font-weight: bold;
	}

	/* Cancelled [-] */
	.markdown-content :global(.task-list-item input[data-task="-"])::before {
		content: '−';
		color: #6b7280;
		font-weight: bold;
	}

	.markdown-content :global(.task-list-item input[data-task="-"]) {
		border-color: #6b7280;
		opacity: 0.6;
	}

	/* Deferred/forwarded [>] */
	.markdown-content :global(.task-list-item input[data-task=">"])::before {
		content: '▶';
		color: #06b6d4;
		font-size: 0.625rem;
	}

	.markdown-content :global(.task-list-item input[data-task=">"]) {
		border-color: #06b6d4;
	}

	/* Scheduling [<] */
	.markdown-content :global(.task-list-item input[data-task="<"])::before {
		content: '◀';
		color: #8b5cf6;
		font-size: 0.625rem;
	}

	.markdown-content :global(.task-list-item input[data-task="<"]) {
		border-color: #8b5cf6;
	}

	/* Question [?] */
	.markdown-content :global(.task-list-item input[data-task="?"])::before {
		content: '?';
		color: #f59e0b;
		font-weight: bold;
	}

	.markdown-content :global(.task-list-item input[data-task="?"]) {
		border-color: #f59e0b;
	}

	/* Important [!] */
	.markdown-content :global(.task-list-item input[data-task="!"])::before {
		content: '!';
		color: #ef4444;
		font-weight: bold;
	}

	.markdown-content :global(.task-list-item input[data-task="!"]) {
		border-color: #ef4444;
		background-color: rgba(239, 68, 68, 0.1);
	}

	/* Star [*] */
	.markdown-content :global(.task-list-item input[data-task="*"])::before {
		content: '★';
		color: #facc15;
	}

	.markdown-content :global(.task-list-item input[data-task="*"]) {
		border-color: #facc15;
	}

	/* Location [l] */
	.markdown-content :global(.task-list-item input[data-task="l"])::before {
		content: '📍';
		font-size: 0.625rem;
	}

	.markdown-content :global(.task-list-item input[data-task="l"]) {
		border-color: #10b981;
	}

	/* Info [i] */
	.markdown-content :global(.task-list-item input[data-task="i"])::before {
		content: 'ⓘ';
		color: #3b82f6;
		font-size: 0.75rem;
	}

	.markdown-content :global(.task-list-item input[data-task="i"]) {
		border-color: #3b82f6;
	}

	/* Savings/money [S] */
	.markdown-content :global(.task-list-item input[data-task="S"])::before {
		content: '$';
		color: #22c55e;
		font-weight: bold;
	}

	.markdown-content :global(.task-list-item input[data-task="S"]) {
		border-color: #22c55e;
	}

	/* Bookmark [b] */
	.markdown-content :global(.task-list-item input[data-task="b"])::before {
		content: '🔖';
		font-size: 0.625rem;
	}

	.markdown-content :global(.task-list-item input[data-task="b"]) {
		border-color: #ec4899;
	}

	/* Quote ["] */
	.markdown-content :global(.task-list-item input[data-task='"'])::before {
		content: '"';
		color: #6b7280;
		font-weight: bold;
	}

	.markdown-content :global(.task-list-item input[data-task='"']) {
		border-color: #6b7280;
	}

	/* ================================
	   Phase C: Embedded Images
	   ================================ */
	.markdown-content :global(.obsidian-embed-image) {
		max-width: 100%;
		height: auto;
		border-radius: 6px;
		margin: 0.5rem 0 1rem;
		box-shadow: 0 2px 8px var(--shadow);
	}

	/* ================================
	   Phase C: Enhanced Note Embed Placeholders
	   ================================ */
	.markdown-content :global(.obsidian-embed-note-found) {
		background-color: hsl(var(--accent) / 0.08);
		border-color: hsl(var(--accent) / 0.3);
		border-style: solid;
	}

	.markdown-content :global(.obsidian-embed-note-name) {
		font-weight: 500;
		color: hsl(var(--foreground));
	}

	.markdown-content :global(.obsidian-embed-note-hint) {
		font-size: 0.75rem;
		color: hsl(var(--muted-foreground));
		font-style: italic;
		margin-left: 0.5rem;
	}

	/* ================================
	   Phase B: Code Block Copy Button
	   ================================ */
	.markdown-content :global(.code-block-container) {
		position: relative;
		margin-bottom: 1rem;
	}

	.markdown-content :global(.code-block-header) {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 0.5rem 1rem;
		background-color: hsl(var(--muted));
		border-top-left-radius: 8px;
		border-top-right-radius: 8px;
		border: 1px solid hsl(var(--border));
		border-bottom: none;
	}

	.markdown-content :global(.code-lang) {
		font-size: 0.75rem;
		font-weight: 600;
		color: hsl(var(--muted-foreground));
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	.markdown-content :global(.code-copy-btn) {
		padding: 0.25rem 0.75rem;
		font-size: 0.75rem;
		font-weight: 500;
		color: hsl(var(--foreground));
		background-color: hsl(var(--background));
		border: 1px solid hsl(var(--border));
		border-radius: 4px;
		cursor: pointer;
		transition: all 0.15s;
	}

	.markdown-content :global(.code-copy-btn:hover) {
		background-color: hsl(var(--secondary));
		border-color: hsl(var(--primary));
	}

	.markdown-content :global(.code-copy-btn.copied) {
		background-color: hsl(var(--primary));
		color: white;
		border-color: hsl(var(--primary));
	}

	.markdown-content :global(.code-block-container pre) {
		margin-top: 0;
		border-top-left-radius: 0;
		border-top-right-radius: 0;
		border-top: none;
	}
</style>
