<script lang="ts">
	import { onMount } from 'svelte';

	interface Heading {
		id: string;
		text: string;
		level: number;
	}

	interface Props {
		content: string;
	}

	let { content }: Props = $props();

	let headings = $state<Heading[]>([]);
	let activeId = $state<string>('');

	// Extract headings from markdown content
	function extractHeadings(markdown: string): Heading[] {
		const headingRegex = /^(#{1,3})\s+(.+)$/gm;
		const result: Heading[] = [];
		let match;

		while ((match = headingRegex.exec(markdown)) !== null) {
			const level = match[1].length;
			const text = match[2].trim();
			const id = text
				.toLowerCase()
				.replace(/[^\w\s-]/g, '')
				.replace(/\s+/g, '-');

			result.push({ id, text, level });
		}

		return result;
	}

	// Set up intersection observer for active heading
	onMount(() => {
		headings = extractHeadings(content);

		const observer = new IntersectionObserver(
			(entries) => {
				entries.forEach((entry) => {
					if (entry.isIntersecting) {
						activeId = entry.target.id;
					}
				});
			},
			{
				rootMargin: '-80px 0px -80% 0px'
			}
		);

		// Observe all headings
		headings.forEach(({ id }) => {
			const element = document.getElementById(id);
			if (element) {
				observer.observe(element);
			}
		});

		return () => {
			observer.disconnect();
		};
	});

	function scrollToHeading(id: string) {
		const element = document.getElementById(id);
		if (element) {
			element.scrollIntoView({ behavior: 'smooth', block: 'start' });
		}
	}
</script>

{#if headings.length > 0}
	<nav class="toc" aria-label="Table of contents">
		<h2 class="toc-title">Contents</h2>
		<ul class="toc-list">
			{#each headings as heading}
				<li class="toc-item level-{heading.level}">
					<button
						class="toc-link"
						class:active={activeId === heading.id}
						onclick={() => scrollToHeading(heading.id)}
					>
						{heading.text}
					</button>
				</li>
			{/each}
		</ul>
	</nav>
{/if}

<style>
	.toc {
		position: sticky;
		top: 2rem;
		padding: 1rem;
		background-color: #f8f9fa;
		border: 1px solid #e0e0e0;
		border-radius: 8px;
		max-height: calc(100vh - 4rem);
		overflow-y: auto;
	}

	.toc-title {
		margin: 0 0 1rem 0;
		font-size: 1rem;
		font-weight: 600;
		color: #333;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	.toc-list {
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.toc-item {
		margin-bottom: 0.25rem;
	}

	.toc-item.level-1 {
		margin-left: 0;
	}

	.toc-item.level-2 {
		margin-left: 1rem;
	}

	.toc-item.level-3 {
		margin-left: 2rem;
	}

	.toc-link {
		display: block;
		width: 100%;
		padding: 0.375rem 0.5rem;
		text-align: left;
		color: #666;
		font-size: 0.875rem;
		line-height: 1.4;
		border: none;
		background: none;
		border-radius: 4px;
		cursor: pointer;
		transition: background-color 0.2s;
	}

	.toc-link:hover {
		background-color: #e8e9ea;
		color: #333;
	}

	.toc-link.active {
		background-color: #0066cc;
		color: white;
		font-weight: 500;
	}

	/* Mobile: hide TOC */
	@media (max-width: 1200px) {
		.toc {
			display: none;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.toc-link {
			transition: none;
		}
	}
</style>
