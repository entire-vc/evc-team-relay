<script lang="ts">
	import { Collapsible, CollapsibleTrigger, CollapsibleContent } from '@evc/ui-svelte';
	import {
		buildFileTree,
		loadExpandedState,
		saveExpandedState,
		type TreeNode,
		type FolderItem
	} from '$lib/file-tree';
	import { browser } from '$app/environment';

	interface Props {
		items: FolderItem[];
		currentSlug: string;
		currentPath?: string;
		onNavigate?: () => void;
	}

	let { items = [], currentSlug = '', currentPath = '', onNavigate }: Props = $props();

	// Build tree from flat items
	const tree = $derived(buildFileTree(items));

	// Track expanded folders
	let expandedPaths = $state<Set<string>>(new Set());

	// Collect all folder paths for default expansion
	function getAllFolderPaths(nodes: TreeNode[]): string[] {
		const paths: string[] = [];
		for (const node of nodes) {
			if (node.type === 'folder') {
				paths.push(node.path);
				if (node.children.length > 0) {
					paths.push(...getAllFolderPaths(node.children));
				}
			}
		}
		return paths;
	}

	// Get ancestor folder paths for the current path
	function getAncestorPaths(path: string): string[] {
		if (!path) return [];
		const parts = path.split('/');
		const ancestors: string[] = [];
		for (let i = 1; i < parts.length; i++) {
			ancestors.push(parts.slice(0, i).join('/'));
		}
		return ancestors;
	}

	// Load expanded state on mount; default to all-expanded on first visit
	$effect(() => {
		if (browser && currentSlug) {
			const saved = loadExpandedState(currentSlug);
			if (saved.size === 0 && tree.length > 0) {
				// First visit: expand all folders
				expandedPaths = new Set(getAllFolderPaths(tree));
				saveExpandedState(currentSlug, expandedPaths);
			} else {
				// Ensure ancestors of current path are always expanded
				const ancestors = getAncestorPaths(currentPath);
				const merged = new Set(saved);
				let changed = false;
				for (const a of ancestors) {
					if (!merged.has(a)) {
						merged.add(a);
						changed = true;
					}
				}
				expandedPaths = merged;
				if (changed) saveExpandedState(currentSlug, merged);
			}
		}
	});

	function toggleExpanded(path: string) {
		const newExpanded = new Set(expandedPaths);
		if (newExpanded.has(path)) {
			newExpanded.delete(path);
		} else {
			newExpanded.add(path);
		}
		expandedPaths = newExpanded;
		saveExpandedState(currentSlug, newExpanded);
	}

	function isExpanded(path: string): boolean {
		return expandedPaths.has(path);
	}

	function getItemUrl(node: TreeNode): string {
		return `/${currentSlug}/${node.path}`;
	}

	function isActive(node: TreeNode): boolean {
		return currentPath === node.path;
	}

	function handleClick() {
		onNavigate?.();
	}
</script>

{#snippet folderIcon(open: boolean)}
	{#if open}
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
			<path
				d="M6 14l1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"
			/>
		</svg>
	{:else}
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
			<path
				d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"
			/>
		</svg>
	{/if}
{/snippet}

{#snippet docIcon()}
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
{/snippet}

{#snippet canvasIcon()}
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
		<rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
		<circle cx="8.5" cy="8.5" r="1.5" />
		<polyline points="21 15 16 10 5 21" />
	</svg>
{/snippet}

{#snippet chevronIcon(open: boolean)}
	<svg
		width="14"
		height="14"
		viewBox="0 0 24 24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		stroke-linejoin="round"
		class="chevron"
		class:open
	>
		<polyline points="9 18 15 12 9 6" />
	</svg>
{/snippet}

{#snippet renderNode(node: TreeNode, depth: number)}
	{#if node.type === 'folder' && node.children.length > 0}
		<!-- Folder with children -->
		<Collapsible open={isExpanded(node.path)} onOpenChange={() => toggleExpanded(node.path)}>
			<CollapsibleTrigger
				class="tree-item folder {isActive(node) ? 'active' : ''}"
				style="--depth: {depth}"
			>
				{@render chevronIcon(isExpanded(node.path))}
				<span class="icon">{@render folderIcon(isExpanded(node.path))}</span>
				<span class="name">{node.name}</span>
			</CollapsibleTrigger>
			<CollapsibleContent>
				<div class="tree-children">
					{#each node.children as child}
						{@render renderNode(child, depth + 1)}
					{/each}
				</div>
			</CollapsibleContent>
		</Collapsible>
	{:else if node.type === 'folder'}
		<!-- Empty folder - link to folder view -->
		<a
			href={getItemUrl(node)}
			class="tree-item folder empty"
			class:active={isActive(node)}
			style="--depth: {depth}"
			onclick={handleClick}
		>
			<span class="chevron-placeholder"></span>
			<span class="icon">{@render folderIcon(false)}</span>
			<span class="name">{node.name}</span>
		</a>
	{:else}
		<!-- Document or Canvas -->
		<a
			href={getItemUrl(node)}
			class="tree-item file"
			class:active={isActive(node)}
			style="--depth: {depth}"
			onclick={handleClick}
		>
			<span class="chevron-placeholder"></span>
			<span class="icon">
				{#if node.type === 'canvas'}
					{@render canvasIcon()}
				{:else}
					{@render docIcon()}
				{/if}
			</span>
			<span class="name">{node.name}</span>
		</a>
	{/if}
{/snippet}

<nav class="file-tree">
	{#if tree.length > 0}
		{#each tree as node}
			{@render renderNode(node, 0)}
		{/each}
	{:else}
		<div class="empty-state">No files</div>
	{/if}
</nav>

<style>
	.file-tree {
		display: flex;
		flex-direction: column;
		gap: 1px;
		font-size: 0.875rem;
	}

	:global(.tree-item) {
		display: flex;
		align-items: center;
		gap: 0.25rem;
		padding: 0.375rem 0.75rem;
		padding-left: calc(0.75rem + var(--depth, 0) * 1rem);
		color: var(--sidebar-foreground);
		text-decoration: none;
		border: none;
		background: transparent;
		width: 100%;
		text-align: left;
		cursor: pointer;
		border-radius: 0.375rem;
		transition: background-color 0.15s ease;
	}

	:global(.tree-item:hover) {
		background-color: var(--sidebar-accent);
	}

	:global(.tree-item:focus-visible) {
		outline: 2px solid var(--sidebar-ring);
		outline-offset: -2px;
	}

	:global(.tree-item.active) {
		background-color: var(--sidebar-accent);
		color: var(--sidebar-accent-foreground);
		font-weight: 500;
	}

	:global(.tree-item .icon) {
		display: flex;
		align-items: center;
		justify-content: center;
		flex-shrink: 0;
		color: var(--muted-foreground);
	}

	:global(.tree-item.active .icon) {
		color: var(--sidebar-primary);
	}

	:global(.tree-item .name) {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		flex: 1;
		min-width: 0;
	}

	.chevron {
		flex-shrink: 0;
		transition: transform 0.2s ease;
		color: var(--muted-foreground);
	}

	.chevron.open {
		transform: rotate(90deg);
	}

	.chevron-placeholder {
		width: 14px;
		flex-shrink: 0;
	}

	.tree-children {
		display: flex;
		flex-direction: column;
		gap: 1px;
	}

	.empty-state {
		padding: 1rem;
		color: var(--muted-foreground);
		font-size: 0.875rem;
		text-align: center;
	}

	/* Folder specific styles */
	:global(.tree-item.folder .icon) {
		color: var(--sidebar-primary);
	}

	/* Animation for collapsible - scoped to file-tree only */
	.file-tree :global([data-collapsible-content]) {
		overflow: hidden;
	}

	.file-tree :global([data-collapsible-content][data-state='open']) {
		animation: slideDown 0.2s ease-out;
	}

	.file-tree :global([data-collapsible-content][data-state='closed']) {
		animation: slideUp 0.2s ease-out;
	}

	@keyframes slideDown {
		from {
			height: 0;
			opacity: 0;
		}
		to {
			height: var(--bits-collapsible-content-height);
			opacity: 1;
		}
	}

	@keyframes slideUp {
		from {
			height: var(--bits-collapsible-content-height);
			opacity: 1;
		}
		to {
			height: 0;
			opacity: 0;
		}
	}
</style>
