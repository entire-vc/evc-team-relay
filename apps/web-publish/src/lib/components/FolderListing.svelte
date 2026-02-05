<script lang="ts">
	interface FolderItem {
		path: string;
		name: string;
		type: 'doc' | 'folder' | 'canvas';
	}

	interface Props {
		items: FolderItem[];
		folderPath: string;
		slug: string;
	}

	let { items, folderPath, slug }: Props = $props();

	function getItemUrl(item: FolderItem): string {
		return `/${slug}/${item.path}`;
	}

	// Group items by type for better organization
	const folders = $derived(items.filter((item) => item.type === 'folder'));
	const documents = $derived(items.filter((item) => item.type === 'doc'));
	const canvases = $derived(items.filter((item) => item.type === 'canvas'));

	function getIcon(type: string): string {
		switch (type) {
			case 'folder':
				return '📁';
			case 'canvas':
				return '🎨';
			default:
				return '📄';
		}
	}

	function getTypeLabel(type: string): string {
		switch (type) {
			case 'folder':
				return 'Folder';
			case 'canvas':
				return 'Canvas';
			default:
				return 'Document';
		}
	}
</script>

<div class="folder-listing">
	<div class="folder-header">
		<h1 class="folder-title">{folderPath}</h1>
		<p class="folder-stats">{items.length} item{items.length !== 1 ? 's' : ''}</p>
	</div>

	{#if items.length === 0}
		<div class="empty-state">
			<span class="empty-icon">📂</span>
			<p class="empty-text">This folder is empty</p>
		</div>
	{:else}
		<!-- Folders first -->
		{#if folders.length > 0}
			<div class="item-group">
				<h3 class="group-title">Folders</h3>
				<ul class="item-list">
					{#each folders as item}
						<li class="item">
							<div class="item-static">
								<span class="item-icon">{getIcon(item.type)}</span>
								<div class="item-info">
									<span class="item-name">{item.name}</span>
									<span class="item-path">{item.path}</span>
								</div>
								<span class="item-type">{getTypeLabel(item.type)}</span>
							</div>
						</li>
					{/each}
				</ul>
			</div>
		{/if}

		<!-- Documents -->
		{#if documents.length > 0}
			<div class="item-group">
				<h3 class="group-title">Documents</h3>
				<ul class="item-list">
					{#each documents as item}
						<li class="item">
							<a href={getItemUrl(item)} class="item-link">
								<span class="item-icon">{getIcon(item.type)}</span>
								<div class="item-info">
									<span class="item-name">{item.name}</span>
									<span class="item-path">{item.path}</span>
								</div>
								<span class="item-type">{getTypeLabel(item.type)}</span>
							</a>
						</li>
					{/each}
				</ul>
			</div>
		{/if}

		<!-- Canvases -->
		{#if canvases.length > 0}
			<div class="item-group">
				<h3 class="group-title">Canvases</h3>
				<ul class="item-list">
					{#each canvases as item}
						<li class="item">
							<a href={getItemUrl(item)} class="item-link">
								<span class="item-icon">{getIcon(item.type)}</span>
								<div class="item-info">
									<span class="item-name">{item.name}</span>
									<span class="item-path">{item.path}</span>
								</div>
								<span class="item-type">{getTypeLabel(item.type)}</span>
							</a>
						</li>
					{/each}
				</ul>
			</div>
		{/if}
	{/if}

</div>

<style>
	.folder-listing {
		max-width: 800px;
		margin: 0 auto;
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

	.folder-header {
		margin-bottom: 2rem;
		padding-bottom: 1rem;
		border-bottom: 2px solid #e0e0e0;
	}

	.folder-title {
		font-size: 1.75rem;
		font-weight: 600;
		color: #222;
		margin: 0 0 0.5rem 0;
	}

	.folder-stats {
		color: #666;
		font-size: 0.9rem;
		margin: 0;
	}

	.empty-state {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		padding: 3rem;
		text-align: center;
		color: #666;
	}

	.empty-icon {
		font-size: 3rem;
		margin-bottom: 1rem;
	}

	.empty-text {
		margin: 0;
	}

	.item-group {
		margin-bottom: 1.5rem;
	}

	.group-title {
		font-size: 0.875rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: #666;
		margin: 0 0 0.75rem 0;
		padding-bottom: 0.5rem;
		border-bottom: 1px solid #eee;
	}

	.item-list {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.item {
		margin-bottom: 0.25rem;
	}

	.item-link {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding: 0.75rem 1rem;
		border-radius: 8px;
		background: #f9f9f9;
		transition: background-color 0.15s, box-shadow 0.15s;
		text-decoration: none;
		color: inherit;
		cursor: pointer;
	}

	.item-link:hover {
		background: #e8f4fd;
		box-shadow: 0 2px 4px rgba(0, 102, 204, 0.1);
	}

	.item-link:hover .item-name {
		color: #0066cc;
	}

	/* Non-clickable items (folders for now) */
	.item-static {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding: 0.75rem 1rem;
		border-radius: 8px;
		background: #f9f9f9;
	}

	.item-icon {
		font-size: 1.25rem;
		flex-shrink: 0;
	}

	.item-info {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0.125rem;
	}

	.item-name {
		font-weight: 500;
		color: #333;
	}

	.item-path {
		font-size: 0.75rem;
		color: #888;
		font-family: monospace;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.item-type {
		font-size: 0.75rem;
		color: #666;
		background: #e8e8e8;
		padding: 0.25rem 0.5rem;
		border-radius: 4px;
		flex-shrink: 0;
	}

	@media (max-width: 768px) {
		.folder-title {
			font-size: 1.5rem;
		}

		.item {
			padding: 0.625rem 0.75rem;
		}

		.item-path {
			display: none;
		}
	}
</style>
