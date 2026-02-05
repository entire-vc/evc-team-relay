<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import MarkdownViewer from './MarkdownViewer.svelte';
	import { connectToRelay, isRealtimeSyncAvailable, type YjsConnection } from '$lib/yjs';
	import { getRelayToken, type RelayToken } from '$lib/api';

	interface Props {
		/** Static content (fallback when real-time not available) */
		content: string;
		/** Document ID for real-time sync (S3RN encoded) */
		docId?: string | null;
		/** Share slug for fetching relay token */
		slug: string;
		/** Session token for protected shares */
		sessionToken?: string;
		class?: string;
	}

	let { content, docId, slug, sessionToken, class: className = '' }: Props = $props();

	let realtimeContent = $state<string | null>(null);
	let connectionStatus = $state<'connecting' | 'connected' | 'disconnected' | 'unavailable'>(
		'unavailable'
	);
	let connection = $state<YjsConnection | null>(null);
	let error = $state<string | null>(null);

	// Use real-time content if available, otherwise fall back to static
	const displayContent = $derived(realtimeContent ?? content);
	const isLive = $derived(connectionStatus === 'connected');

	onMount(async () => {
		if (!isRealtimeSyncAvailable(docId)) {
			connectionStatus = 'unavailable';
			return;
		}

		connectionStatus = 'connecting';

		try {
			// Get relay token from control plane
			const token = await getRelayToken(slug, sessionToken);

			// Connect to y-sweet relay
			const conn = connectToRelay(token);
			connection = conn;

			// Subscribe to content updates
			conn.content.subscribe((value) => {
				if (value) {
					realtimeContent = value;
				}
			});

			// Subscribe to status updates
			conn.status.subscribe((value) => {
				connectionStatus = value;
			});
		} catch (err) {
			console.error('[LiveMarkdownViewer] Failed to connect:', err);
			error = err instanceof Error ? err.message : 'Failed to connect to real-time sync';
			connectionStatus = 'unavailable';
		}
	});

	onDestroy(() => {
		if (connection) {
			connection.destroy();
			connection = null;
		}
	});
</script>

<div class="live-viewer-container">
	{#if isLive}
		<div class="live-indicator">
			<span class="live-dot"></span>
			<span class="live-text">Live</span>
		</div>
	{/if}

	<MarkdownViewer content={displayContent} class={className} />

	{#if error && !realtimeContent}
		<div class="sync-error">
			<span class="error-icon">!</span>
			<span class="error-text">Real-time sync unavailable. Showing cached content.</span>
		</div>
	{/if}
</div>

<style>
	.live-viewer-container {
		position: relative;
	}

	.live-indicator {
		position: fixed;
		top: 1rem;
		right: 1rem;
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.5rem 1rem;
		background-color: rgba(46, 125, 50, 0.9);
		color: white;
		border-radius: 20px;
		font-size: 0.875rem;
		font-weight: 500;
		z-index: 100;
		box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
		animation: fadeIn 0.3s ease-out;
	}

	@keyframes fadeIn {
		from {
			opacity: 0;
			transform: translateY(-10px);
		}
		to {
			opacity: 1;
			transform: translateY(0);
		}
	}

	.live-dot {
		width: 8px;
		height: 8px;
		background-color: #fff;
		border-radius: 50%;
		animation: pulse 2s infinite;
	}

	@keyframes pulse {
		0%,
		100% {
			opacity: 1;
		}
		50% {
			opacity: 0.5;
		}
	}

	.live-text {
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	.sync-error {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-top: 1rem;
		padding: 0.75rem 1rem;
		background-color: #fff3e0;
		border-radius: 4px;
		color: #e65100;
		font-size: 0.875rem;
	}

	.error-icon {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 20px;
		height: 20px;
		background-color: #e65100;
		color: white;
		border-radius: 50%;
		font-weight: bold;
		font-size: 0.75rem;
	}

	/* Reduce motion for accessibility */
	@media (prefers-reduced-motion: reduce) {
		.live-indicator {
			animation: none;
		}

		.live-dot {
			animation: none;
		}
	}

	/* Mobile responsiveness */
	@media (max-width: 768px) {
		.live-indicator {
			top: 0.5rem;
			right: 0.5rem;
			padding: 0.375rem 0.75rem;
			font-size: 0.8125rem;
		}
	}
</style>
