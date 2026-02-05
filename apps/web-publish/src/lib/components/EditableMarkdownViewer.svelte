<script lang="ts">
	import { onMount } from 'svelte';
	import { renderMarkdown } from '$lib/markdown';
	import { updateShareContent } from '$lib/api';

	interface Props {
		content: string;
		slug: string;
		canEdit: boolean;
		sessionToken?: string;
		authToken?: string;
		class?: string;
	}

	let {
		content = $bindable(),
		slug,
		canEdit = false,
		sessionToken,
		authToken,
		class: className = ''
	}: Props = $props();

	let renderedHtml = $state('');
	let isRendering = $state(true);
	let isEditing = $state(false);
	let editContent = $state('');
	let isSaving = $state(false);
	let saveError = $state('');

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

	// Re-render when content changes (external updates)
	$effect(() => {
		if (content && !isEditing) {
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

	function startEditing() {
		editContent = content;
		isEditing = true;
		saveError = '';
	}

	function cancelEditing() {
		isEditing = false;
		editContent = '';
		saveError = '';
	}

	async function saveChanges() {
		if (!editContent.trim()) {
			saveError = 'Content cannot be empty';
			return;
		}

		isSaving = true;
		saveError = '';

		try {
			await updateShareContent(slug, editContent, sessionToken, authToken);
			content = editContent;
			isEditing = false;
			editContent = '';
		} catch (error) {
			saveError = error instanceof Error ? error.message : 'Failed to save changes';
			console.error('Failed to save content:', error);
		} finally {
			isSaving = false;
		}
	}
</script>

{#if isEditing}
	<!-- Edit mode -->
	<div class="editor-container">
		<div class="editor-header">
			<h3>Editing Document</h3>
			<div class="editor-actions">
				<button class="btn btn-secondary" onclick={cancelEditing} disabled={isSaving}>
					Cancel
				</button>
				<button class="btn btn-primary" onclick={saveChanges} disabled={isSaving}>
					{isSaving ? 'Saving...' : 'Save'}
				</button>
			</div>
		</div>

		{#if saveError}
			<div class="error-banner">
				{saveError}
			</div>
		{/if}

		<textarea class="editor-textarea" bind:value={editContent} disabled={isSaving}></textarea>
	</div>
{:else}
	<!-- View mode -->
	<div class="viewer-container">
		{#if canEdit}
			<button class="edit-button" onclick={startEditing} title="Edit document">
				<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
					<path
						d="M11.333 2.00004C11.5081 1.82494 11.716 1.68605 11.9447 1.59129C12.1735 1.49653 12.4187 1.44775 12.6663 1.44775C12.914 1.44775 13.1592 1.49653 13.3879 1.59129C13.6167 1.68605 13.8246 1.82494 13.9997 2.00004C14.1748 2.17513 14.3137 2.383 14.4084 2.61178C14.5032 2.84055 14.552 3.08575 14.552 3.33337C14.552 3.58099 14.5032 3.82619 14.4084 4.05497C14.3137 4.28374 14.1748 4.49161 13.9997 4.66671L5.33301 13.3334L2.66634 14L3.33301 11.3334L11.333 2.00004Z"
						stroke="currentColor"
						stroke-width="1.5"
						stroke-linecap="round"
						stroke-linejoin="round"
					/>
				</svg>
				<span>Edit</span>
			</button>
		{/if}

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
	</div>
{/if}

<style>
	/* Viewer styles */
	.viewer-container {
		position: relative;
	}

	.edit-button {
		position: absolute;
		top: -3rem;
		right: 0;
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.5rem 1rem;
		background-color: #f5f5f5;
		border: 1px solid #ddd;
		border-radius: 4px;
		cursor: pointer;
		font-size: 0.875rem;
		font-weight: 500;
		color: #333;
		transition:
			background-color 0.2s,
			border-color 0.2s;
	}

	.edit-button:hover {
		background-color: #0066cc;
		color: white;
		border-color: #0066cc;
	}

	.edit-button svg {
		width: 16px;
		height: 16px;
	}

	/* Editor styles */
	.editor-container {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.editor-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 1rem;
		background-color: #f5f5f5;
		border-radius: 4px;
	}

	.editor-header h3 {
		margin: 0;
		font-size: 1.125rem;
		font-weight: 600;
		color: #333;
	}

	.editor-actions {
		display: flex;
		gap: 0.5rem;
	}

	.btn {
		padding: 0.5rem 1rem;
		border: 1px solid transparent;
		border-radius: 4px;
		font-size: 0.875rem;
		font-weight: 500;
		cursor: pointer;
		transition:
			background-color 0.2s,
			border-color 0.2s;
	}

	.btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.btn-primary {
		background-color: #0066cc;
		color: white;
	}

	.btn-primary:hover:not(:disabled) {
		background-color: #0052a3;
	}

	.btn-secondary {
		background-color: white;
		color: #333;
		border-color: #ddd;
	}

	.btn-secondary:hover:not(:disabled) {
		background-color: #f5f5f5;
	}

	.error-banner {
		padding: 0.75rem 1rem;
		background-color: #ffebee;
		color: #c62828;
		border: 1px solid #ef9a9a;
		border-radius: 4px;
		font-size: 0.875rem;
	}

	.editor-textarea {
		width: 100%;
		min-height: 500px;
		padding: 1rem;
		border: 1px solid #ddd;
		border-radius: 4px;
		font-family: 'Monaco', 'Courier New', monospace;
		font-size: 0.9375rem;
		line-height: 1.6;
		resize: vertical;
	}

	.editor-textarea:focus {
		outline: none;
		border-color: #0066cc;
		box-shadow: 0 0 0 3px rgba(0, 102, 204, 0.1);
	}

	.editor-textarea:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	/* Loading skeleton */
	.markdown-loading {
		padding: 2rem 0;
	}

	.skeleton-container {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.skeleton {
		background: linear-gradient(90deg, #f0f0f0 25%, #e8e8e8 50%, #f0f0f0 75%);
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

	/* Markdown content styles */
	.markdown-content {
		line-height: 1.6;
		color: #333;
		animation: fadeIn 0.3s ease-out;
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
		border-bottom: 1px solid #eee;
		padding-bottom: 0.3rem;
	}

	.markdown-content :global(h2) {
		font-size: 1.5rem;
		margin-top: 1.5rem;
		margin-bottom: 0.75rem;
		font-weight: 600;
	}

	.markdown-content :global(h3) {
		font-size: 1.25rem;
		margin-top: 1.25rem;
		margin-bottom: 0.5rem;
		font-weight: 600;
	}

	.markdown-content :global(h4),
	.markdown-content :global(h5),
	.markdown-content :global(h6) {
		font-size: 1rem;
		margin-top: 1rem;
		margin-bottom: 0.5rem;
		font-weight: 600;
	}

	.markdown-content :global(p) {
		margin-bottom: 1rem;
	}

	.markdown-content :global(a) {
		color: #0066cc;
		text-decoration: none;
	}

	.markdown-content :global(a:hover) {
		text-decoration: underline;
	}

	.markdown-content :global(code) {
		background-color: #f5f5f5;
		padding: 0.2em 0.4em;
		border-radius: 3px;
		font-family: 'Courier New', monospace;
		font-size: 0.9em;
	}

	.markdown-content :global(pre) {
		background-color: #282c34;
		padding: 1rem;
		border-radius: 8px;
		overflow-x: auto;
		margin-bottom: 1rem;
		box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
		position: relative;
	}

	.markdown-content :global(pre code) {
		background-color: transparent;
		padding: 0;
		color: #abb2bf;
		font-size: 0.875rem;
		line-height: 1.5;
	}

	.markdown-content :global(blockquote) {
		border-left: 4px solid #ddd;
		padding-left: 1rem;
		margin-left: 0;
		color: #666;
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
		border: 1px solid #ddd;
		padding: 0.5rem;
		text-align: left;
	}

	.markdown-content :global(th) {
		background-color: #f5f5f5;
		font-weight: 600;
	}

	.markdown-content :global(img) {
		max-width: 100%;
		height: auto;
	}

	.markdown-content :global(hr) {
		border: none;
		border-top: 1px solid #ddd;
		margin: 2rem 0;
	}

	.markdown-content :global(.error) {
		color: #d32f2f;
		padding: 1rem;
		background-color: #ffebee;
		border-radius: 5px;
	}

	/* Mobile responsiveness */
	@media (max-width: 768px) {
		.edit-button {
			top: auto;
			bottom: -3rem;
			right: auto;
			left: 0;
		}

		.editor-header {
			flex-direction: column;
			gap: 1rem;
			align-items: flex-start;
		}

		.editor-actions {
			width: 100%;
		}

		.btn {
			flex: 1;
		}

		.editor-textarea {
			min-height: 400px;
			font-size: 0.875rem;
		}

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

		.btn,
		.edit-button {
			transition: none;
		}
	}
</style>
