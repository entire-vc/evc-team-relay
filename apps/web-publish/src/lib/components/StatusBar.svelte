<script lang="ts">
	import { Badge, Button } from '@evc/ui-svelte';

	interface Props {
		visibility: string;
		updatedAt: string | null;
		showBackButton?: boolean;
		backUrl?: string;
		shareUrl: string;
	}

	let { visibility, updatedAt, showBackButton = false, backUrl = '', shareUrl }: Props = $props();

	// Format timestamp
	const formattedDate = $derived(() => {
		if (!updatedAt) return 'Never';
		const date = new Date(updatedAt);
		const timeStr = date.toLocaleTimeString('en-US', {
			hour: '2-digit',
			minute: '2-digit',
			hour12: false
		});
		const dateStr = date.toLocaleDateString('en-US', {
			year: 'numeric',
			month: 'short',
			day: 'numeric'
		});
		return `${dateStr}, ${timeStr}`;
	});

	// Visibility badge config
	type BadgeVariant = 'success' | 'warning' | 'destructive' | 'secondary' | 'default';
	const visibilityConfig = $derived((): { label: string; variant: BadgeVariant } => {
		switch (visibility) {
			case 'public':
				return { label: 'Public', variant: 'secondary' };
			case 'protected':
				return { label: 'Protected', variant: 'warning' };
			case 'private':
				return { label: 'Private', variant: 'destructive' };
			default:
				return { label: 'Public', variant: 'secondary' };
		}
	});

	let copyButtonText = $state('Copy link');

	function copyLink() {
		navigator.clipboard.writeText(shareUrl);
		copyButtonText = 'Copied!';
		setTimeout(() => {
			copyButtonText = 'Copy link';
		}, 2000);
	}
</script>

<div class="status-bar">
	<div class="status-items">
		<!-- Date -->
		<span class="status-item date">
			<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
				<rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
			</svg>
			<span>{formattedDate()}</span>
		</span>

		<!-- Visibility -->
		<span class="status-item visibility">
			{#if visibility === 'public'}
				<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
					<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
				</svg>
			{:else if visibility === 'protected'}
				<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
					<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
				</svg>
			{:else}
				<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
					<rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
				</svg>
			{/if}
			<Badge variant={visibilityConfig().variant} class="badge-compact">{visibilityConfig().label}</Badge>
		</span>

		<!-- Copy Link -->
		<Button variant="ghost" size="sm" onclick={copyLink} class="btn-compact">
			<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
				<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
			</svg>
			{copyButtonText}
		</Button>

		<!-- Back to folder -->
		{#if showBackButton && backUrl}
			<Button variant="ghost" size="sm" onclick={() => (window.location.href = backUrl)} class="btn-compact">
				<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
					<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>
				</svg>
				Back
			</Button>
		{/if}
	</div>
</div>

<style>
	.status-bar {
		padding: 0.5rem 0;
		border-bottom: 1px solid hsl(var(--border));
		margin-bottom: 1rem;
	}

	.status-items {
		display: flex;
		gap: 1rem;
		align-items: center;
		flex-wrap: wrap;
	}

	.status-item {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
		font-size: 0.8125rem;
		color: hsl(var(--muted-foreground));
	}

	.status-item svg {
		flex-shrink: 0;
	}

	.status-item.date {
		color: hsl(var(--muted-foreground));
	}

	.status-item.visibility {
		gap: 0.25rem;
	}

	:global(.badge-compact) {
		padding: 0.125rem 0.5rem !important;
		font-size: 0.75rem !important;
	}

	:global(.btn-compact) {
		padding: 0.25rem 0.5rem !important;
		font-size: 0.75rem !important;
		gap: 0.25rem !important;
		height: auto !important;
	}
</style>
