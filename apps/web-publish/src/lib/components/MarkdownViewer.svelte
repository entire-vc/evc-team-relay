<script lang="ts">
	import { onMount } from 'svelte';
	import { renderMarkdown } from '$lib/markdown';

	interface Props {
		content: string;
		class?: string;
	}

	let { content, class: className = '' }: Props = $props();

	let renderedHtml = $state('');
	let isRendering = $state(true);

	onMount(async () => {
		try {
			renderedHtml = await renderMarkdown(content);
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
			renderMarkdown(content)
				.then((html) => {
					renderedHtml = html;
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
	<div class="markdown-content {className}">
		{@html renderedHtml}
	</div>
{/if}

<style>
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
		color: var(--primary);
		text-decoration: none;
	}

	.markdown-content :global(a:hover) {
		color: var(--primary-hover);
		text-decoration: underline;
	}

	.markdown-content :global(code) {
		background-color: var(--code-inline-bg);
		padding: 0.2em 0.4em;
		border-radius: 3px;
		font-family: 'Courier New', monospace;
		font-size: 0.9em;
		color: var(--code-inline-fg);
	}

	.markdown-content :global(pre) {
		background-color: var(--code-bg);
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
		color: var(--code-fg);
		font-size: 0.875rem;
		line-height: 1.5;
	}

	.markdown-content :global(blockquote) {
		border-left: 4px solid var(--border);
		padding-left: 1rem;
		margin-left: 0;
		color: var(--muted-foreground);
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
		border: 1px solid var(--border);
		padding: 0.5rem;
		text-align: left;
	}

	.markdown-content :global(th) {
		background-color: var(--secondary);
		font-weight: 600;
		color: var(--secondary-foreground);
	}

	.markdown-content :global(img) {
		max-width: 100%;
		height: auto;
	}

	.markdown-content :global(hr) {
		border: none;
		border-top: 1px solid var(--border);
		margin: 2rem 0;
	}

	.markdown-content :global(.error) {
		color: var(--destructive);
		padding: 1rem;
		background-color: rgba(239, 68, 68, 0.1);
		border-radius: 5px;
	}

	/* Responsive tables */
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
	}

	/* Reduce motion for accessibility */
	@media (prefers-reduced-motion: reduce) {
		.skeleton {
			animation: none;
		}

		.markdown-content {
			animation: none;
		}
	}
</style>
