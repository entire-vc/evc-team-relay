<script lang="ts">
	import '../app.css';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import LoadingBar from '$lib/components/LoadingBar.svelte';
	import { page } from '$app/stores';
	import { browser } from '$app/environment';

	let { children, data } = $props();

	// Determine if user is authenticated (stub for now)
	let isAuthenticated = $state(false);

	// Check if on home page
	const isHomePage = $derived($page.url.pathname === '/');

	// Extract folder items and slug from page data (if available)
	// Note: folderItems comes from page data, not layout data, so we use $page.data
	const folderItems = $derived($page.data?.folderItems);
	const currentSlug = $derived($page.data?.share?.web_slug);
	// Current path for highlighting active item in file tree
	const currentPath = $derived($page.data?.filePath || '');

	// Get branding from server info (from layout load)
	const branding = $derived(data?.serverInfo?.branding);

	// Track sidebar collapsed state for main content margin
	let sidebarCollapsed = $state(false);

	// Sync with localStorage on mount and changes
	$effect(() => {
		if (browser) {
			const checkCollapsed = () => {
				sidebarCollapsed = localStorage.getItem('sidebar-collapsed') === 'true';
			};
			checkCollapsed();
			// Listen for storage changes (in case of multiple tabs)
			window.addEventListener('storage', checkCollapsed);
			// Also poll periodically for same-tab changes
			const interval = setInterval(checkCollapsed, 100);
			return () => {
				window.removeEventListener('storage', checkCollapsed);
				clearInterval(interval);
			};
		}
	});
</script>

<svelte:head>
	<link
		rel="stylesheet"
		href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/styles/github.min.css"
	/>
	<meta name="viewport" content="width=device-width, initial-scale=1.0" />
</svelte:head>

<LoadingBar />

<div class="app-container">
	{#if !isHomePage}
		<Sidebar {isAuthenticated} {folderItems} {currentSlug} {currentPath} {branding} />
	{/if}
	<main class="main-content" class:collapsed={sidebarCollapsed} class:home-page={isHomePage}>
		<div class="content-wrapper" class:home-page={isHomePage}>
			{@render children()}
		</div>
	</main>
</div>

<style>
	:global(body) {
		margin: 0;
		padding: 0;
		font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial,
			sans-serif;
		background-color: var(--background);
		color: var(--foreground);
		line-height: 1.6;
	}

	:global(*) {
		box-sizing: border-box;
	}

	:global(html) {
		scroll-behavior: smooth;
	}

	:global(:focus-visible) {
		outline: 2px solid var(--ring);
		outline-offset: 2px;
	}

	.app-container {
		display: flex;
		min-height: 100vh;
	}

	.main-content {
		flex: 1;
		margin-left: 250px;
		padding: 2rem;
		transition: margin-left 0.3s ease-out;
	}

	.main-content.collapsed {
		margin-left: 60px;
	}

	.main-content.home-page {
		margin-left: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		min-height: 100vh;
	}

	.content-wrapper {
		max-width: 900px;
		margin: 0 auto;
	}

	.content-wrapper.home-page {
		max-width: 1200px;
		width: 100%;
	}

	/* Mobile responsiveness */
	@media (max-width: 768px) {
		.app-container {
			flex-direction: column;
		}

		.main-content {
			margin-left: 0;
			padding: 1rem;
		}
	}

	/* Tablet */
	@media (min-width: 769px) and (max-width: 1024px) {
		.main-content {
			padding: 1.5rem;
		}
	}

	/* Reduce motion for accessibility */
	@media (prefers-reduced-motion: reduce) {
		:global(html) {
			scroll-behavior: auto;
		}
	}
</style>
