<script lang="ts">
	interface Props {
		text: string;
		label?: string;
		class?: string;
	}

	let { text, label = 'Copy', class: className = '' }: Props = $props();

	let copied = $state(false);
	let timeout: ReturnType<typeof setTimeout> | null = null;

	async function handleCopy() {
		try {
			await navigator.clipboard.writeText(text);
			copied = true;

			// Clear previous timeout if exists
			if (timeout) {
				clearTimeout(timeout);
			}

			// Reset after 2 seconds
			timeout = setTimeout(() => {
				copied = false;
				timeout = null;
			}, 2000);
		} catch (error) {
			console.error('Failed to copy:', error);
		}
	}
</script>

<button
	type="button"
	class="copy-button {className}"
	onclick={handleCopy}
	aria-label={copied ? 'Copied!' : label}
>
	{#if copied}
		<span class="icon">✓</span>
		<span class="text">Copied!</span>
	{:else}
		<span class="icon">📋</span>
		<span class="text">{label}</span>
	{/if}
</button>

<style>
	.copy-button {
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

	.copy-button:hover {
		background-color: #e8e9ea;
		border-color: #ccc;
	}

	.copy-button:active {
		transform: scale(0.98);
	}

	.icon {
		font-size: 1rem;
		line-height: 1;
	}

	.text {
		line-height: 1;
	}

	@media (prefers-reduced-motion: reduce) {
		.copy-button {
			transition: none;
		}

		.copy-button:active {
			transform: none;
		}
	}
</style>
