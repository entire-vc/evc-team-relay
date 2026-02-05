<script lang="ts">
	import { page } from '$app/stores';
	import { browser } from '$app/environment';
	import FileTree from './FileTree.svelte';
	import type { FolderItem } from '$lib/file-tree';

	interface Branding {
		name: string;
		logo_url: string;
		favicon_url: string;
	}

	interface Props {
		isAuthenticated?: boolean;
		shareName?: string;
		folderItems?: FolderItem[];
		currentSlug?: string;
		currentPath?: string;
		branding?: Branding;
	}

	let {
		isAuthenticated = false,
		shareName = '',
		folderItems = [],
		currentSlug = '',
		currentPath = '',
		branding
	}: Props = $props();

	// Menu state for mobile
	let menuOpen = $state(false);

	// Collapse state - persisted in localStorage
	let isCollapsed = $state(false);

	// Load collapse state from localStorage on mount
	$effect(() => {
		if (browser) {
			const saved = localStorage.getItem('sidebar-collapsed');
			if (saved !== null) {
				isCollapsed = saved === 'true';
			}
		}
	});

	function toggleMenu() {
		menuOpen = !menuOpen;
	}

	function closeMenu() {
		menuOpen = false;
	}

	function toggleCollapse() {
		isCollapsed = !isCollapsed;
		if (browser) {
			localStorage.setItem('sidebar-collapsed', String(isCollapsed));
		}
	}

	// Close menu when navigating
	$effect(() => {
		if ($page.url) {
			closeMenu();
		}
	});
</script>

<!-- Mobile hamburger menu button -->
<button class="hamburger" class:open={menuOpen} onclick={toggleMenu} aria-label="Toggle menu">
	<span></span>
	<span></span>
	<span></span>
</button>

<!-- Collapse toggle button (desktop) -->
<button
	class="collapse-toggle"
	class:collapsed={isCollapsed}
	onclick={toggleCollapse}
	aria-label={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
	title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
>
	<span class="collapse-icon">{isCollapsed ? '→' : '←'}</span>
</button>

<aside class="sidebar" class:open={menuOpen} class:collapsed={isCollapsed}>
	<div class="sidebar-header">
		<a href="/" class="logo-link">
			<div class="brand-container">
				{#if branding?.logo_url}
					<img src={branding.logo_url} alt="{branding.name} logo" class="brand-logo" />
				{:else}
					<svg class="brand-logo" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
						<circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" />
						<path
							d="M12 6v12M6 12h12"
							stroke="currentColor"
							stroke-width="2"
							stroke-linecap="round"
						/>
					</svg>
				{/if}
				{#if !isCollapsed}
					<div class="brand-text">
						<h1 class="site-title">{branding?.name || 'Team Relay'}</h1>
					</div>
				{/if}
			</div>
		</a>
	</div>

	<nav class="sidebar-nav">
		{#if folderItems && folderItems.length > 0}
			<!-- File tree navigation -->
			{#if !isCollapsed}
				<div class="nav-section">
					<div class="nav-section-header">Contents</div>
				</div>
				<FileTree items={folderItems} {currentSlug} {currentPath} onNavigate={closeMenu} />
			{:else}
				<!-- Collapsed: show icon-only hint -->
				<div class="collapsed-hint" title="Expand to see contents">
					<svg
						width="20"
						height="20"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						<path
							d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"
						/>
					</svg>
				</div>
			{/if}
		{:else if isAuthenticated}
			<!-- Default authenticated nav -->
			<a href="/" class="nav-link" onclick={closeMenu}>
				<span class="nav-icon">
					<svg
						width="16"
						height="16"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
						<polyline points="14 2 14 8 20 8" />
					</svg>
				</span>
				{#if !isCollapsed}<span class="nav-text">My Shares</span>{/if}
			</a>
			<a href="/settings" class="nav-link" onclick={closeMenu}>
				<span class="nav-icon">
					<svg
						width="16"
						height="16"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						<circle cx="12" cy="12" r="3" />
						<path
							d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"
						/>
					</svg>
				</span>
				{#if !isCollapsed}<span class="nav-text">Settings</span>{/if}
			</a>
		{:else if !isCollapsed}
			<!-- Guest hint -->
			<div class="nav-hint">
				<p>Share documents with your team or publicly.</p>
				<p class="hint-subtext">Sign in to manage your shares.</p>
			</div>
		{/if}
	</nav>

	<div class="sidebar-footer">
		{#if !isCollapsed}
			<a href="https://github.com/entire-vc" target="_blank" rel="noopener" class="powered-by-link">
				<span class="powered-by">Entire VC Team Relay</span>
			</a>
		{/if}
	</div>
</aside>

<!-- Overlay for mobile -->
{#if menuOpen}
	<div
		class="overlay"
		onclick={closeMenu}
		onkeydown={(e) => e.key === 'Escape' && closeMenu()}
		role="button"
		tabindex="-1"
		aria-label="Close menu"
	></div>
{/if}

<style>
	.hamburger {
		display: none;
		position: fixed;
		top: 1rem;
		left: 1rem;
		z-index: 1002;
		background: var(--background);
		border: 1px solid var(--sidebar-border);
		border-radius: 6px;
		padding: 0.5rem;
		cursor: pointer;
		flex-direction: column;
		gap: 4px;
		width: 40px;
		height: 40px;
		transition: background-color 0.2s;
	}

	.hamburger:hover {
		background-color: var(--sidebar-accent);
	}

	.hamburger span {
		display: block;
		width: 20px;
		height: 2px;
		background-color: var(--foreground);
		transition:
			transform 0.2s,
			opacity 0.2s;
	}

	.hamburger.open span:nth-child(1) {
		transform: translateY(6px) rotate(45deg);
	}

	.hamburger.open span:nth-child(2) {
		opacity: 0;
	}

	.hamburger.open span:nth-child(3) {
		transform: translateY(-6px) rotate(-45deg);
	}

	.collapse-toggle {
		display: flex;
		align-items: center;
		justify-content: center;
		position: fixed;
		left: 238px;
		top: 50%;
		transform: translateY(-50%);
		z-index: 1001;
		width: 24px;
		height: 48px;
		background: var(--sidebar-accent);
		border: 1px solid var(--sidebar-border);
		border-left: none;
		border-radius: 0 6px 6px 0;
		cursor: pointer;
		transition:
			left 0.3s ease-out,
			background-color 0.2s;
	}

	.collapse-toggle:hover {
		background-color: var(--secondary);
	}

	.collapse-toggle.collapsed {
		left: 48px;
	}

	.collapse-icon {
		font-size: 0.875rem;
		color: var(--muted-foreground);
	}

	.overlay {
		display: none;
	}

	.sidebar {
		width: 250px;
		height: 100vh;
		background-color: var(--sidebar-background);
		border-right: 1px solid var(--border);
		box-shadow: 1px 0 3px rgba(0, 0, 0, 0.05);
		display: flex;
		flex-direction: column;
		position: fixed;
		left: 0;
		top: 0;
		overflow-y: auto;
		overflow-x: hidden;
		z-index: 1000;
		transition: width 0.3s ease-out;
	}

	.sidebar.collapsed {
		width: 60px;
	}

	.sidebar-header {
		padding: 1.5rem 1rem;
		border-bottom: 1px solid var(--sidebar-border);
	}

	.sidebar.collapsed .sidebar-header {
		padding: 1rem 0.5rem;
	}

	.logo-link {
		text-decoration: none;
		color: inherit;
		display: block;
	}

	.brand-container {
		display: flex;
		align-items: center;
		gap: 0.75rem;
	}

	.brand-logo {
		width: 32px;
		height: 32px;
		color: var(--sidebar-foreground);
		flex-shrink: 0;
		object-fit: contain;
	}

	.brand-text {
		display: flex;
		flex-direction: column;
	}

	.site-title {
		font-size: 1.25rem;
		font-weight: 700;
		margin: 0;
		color: var(--foreground);
		line-height: 1.2;
		transition: color 0.2s;
	}

	.logo-link:hover .site-title {
		color: var(--primary);
	}

	.sidebar-nav {
		flex: 1;
		padding: 0.5rem 0;
		overflow-y: auto;
	}

	.nav-link {
		display: flex;
		align-items: center;
		padding: 0.75rem 1rem;
		color: var(--sidebar-foreground);
		text-decoration: none;
		transition: background-color 0.2s;
		border-left: 3px solid transparent;
	}

	.sidebar.collapsed .nav-link {
		justify-content: center;
		padding: 0.75rem 0;
	}

	.nav-link:hover {
		background-color: var(--sidebar-accent);
		border-left-color: var(--sidebar-primary);
		color: var(--sidebar-accent-foreground);
	}

	.nav-link:focus-visible {
		outline: none;
		background-color: var(--sidebar-accent);
		border-left-color: var(--sidebar-primary);
		color: var(--sidebar-accent-foreground);
	}

	.nav-icon {
		margin-right: 0.75rem;
		display: flex;
		align-items: center;
		justify-content: center;
		width: 16px;
		height: 16px;
		flex-shrink: 0;
	}

	.nav-icon svg {
		width: 16px;
		height: 16px;
	}

	.sidebar.collapsed .nav-icon {
		margin-right: 0;
	}

	.nav-text {
		font-size: 0.9375rem;
		font-weight: 500;
	}

	.nav-hint {
		padding: 1rem;
		color: var(--muted-foreground);
		font-size: 0.875rem;
		line-height: 1.5;
	}

	.hint-subtext {
		color: var(--muted-foreground);
		opacity: 0.7;
		font-size: 0.8125rem;
		margin-top: 0.5rem;
	}

	/* Navigation section styles */
	.nav-section {
		margin-bottom: 0.25rem;
	}

	.nav-section-header {
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--muted-foreground);
		padding: 0.5rem 1rem;
		border-bottom: 1px solid var(--sidebar-border);
	}

	.collapsed-hint {
		display: flex;
		justify-content: center;
		padding: 1rem 0;
		color: var(--muted-foreground);
	}

	.sidebar-footer {
		padding: 1rem;
		border-top: 1px solid var(--sidebar-border);
		margin-top: auto;
	}

	.powered-by-link {
		text-decoration: none;
		display: block;
		text-align: left;
	}

	.powered-by {
		font-size: 0.75rem;
		color: var(--muted-foreground);
		opacity: 0.7;
		margin: 0;
		transition:
			color 0.2s,
			opacity 0.2s;
	}

	.powered-by-link:hover .powered-by {
		color: var(--sidebar-foreground);
		opacity: 1;
	}

	/* Mobile responsiveness */
	@media (max-width: 768px) {
		.hamburger {
			display: flex;
		}

		.collapse-toggle {
			display: none;
		}

		.sidebar {
			transform: translateX(-100%);
			transition: transform 0.3s ease-out;
			box-shadow: 2px 0 8px var(--shadow);
			z-index: 1001;
			width: 250px;
		}

		.sidebar.open {
			transform: translateX(0);
		}

		.sidebar.collapsed {
			width: 250px;
		}

		.overlay {
			display: block;
			position: fixed;
			top: 0;
			left: 0;
			right: 0;
			bottom: 0;
			background-color: rgba(0, 0, 0, 0.5);
			z-index: 999;
			animation: fadeIn 0.3s;
		}

		@keyframes fadeIn {
			from {
				opacity: 0;
			}
			to {
				opacity: 1;
			}
		}
	}

	/* Reduce motion for accessibility */
	@media (prefers-reduced-motion: reduce) {
		.hamburger,
		.hamburger span,
		.sidebar,
		.nav-link,
		.overlay,
		.collapse-toggle {
			transition: none;
		}

		.overlay {
			animation: none;
		}
	}
</style>
