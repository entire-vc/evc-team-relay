<script lang="ts">
	// Self-hosted (same origin) stylesheets for rendered markdown. The CSP is
	// style-src 'self', so the CDN links these replaced were blocked on every
	// page — code rendered unthemed, formulas unstyled (raw MathML shown next to
	// the HTML), plus a console error per page. Emitted as hashed assets and
	// linked only when the rendered body actually contains math / code, so a
	// plain page pays for neither (#cee667d8).
	import katexCssUrl from 'katex/dist/katex.min.css?url';
	// Code blocks sit on --code-bg, a dark surface in both colour schemes, so the
	// dark token palette is the one with readable contrast (github.css puts dark
	// blue strings/comments on that dark background).
	import hljsCssUrl from 'highlight.js/styles/github-dark.css?url';

	let { html }: { html: string } = $props();

	const hasMath = $derived(html.includes('class="katex'));
	const hasCode = $derived(html.includes('class="hljs'));
</script>

<svelte:head>
	{#if hasMath}
		<link rel="stylesheet" href={katexCssUrl} />
	{/if}
	{#if hasCode}
		<link rel="stylesheet" href={hljsCssUrl} />
	{/if}
</svelte:head>
