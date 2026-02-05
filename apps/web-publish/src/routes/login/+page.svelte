<script lang="ts">
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<svelte:head>
	<title>Sign In - Relay</title>
</svelte:head>

<div class="login-container">
	<div class="login-content">
		<div class="login-icon">🔐</div>

		<h1 class="login-title">Sign In</h1>

		{#if data.error}
			<div class="error-message">
				<p>{data.error}</p>
			</div>

			<p class="login-hint">
				Please contact the system administrator to configure OAuth authentication,
				or ask the document owner to change visibility to "protected" (password-based).
			</p>
		{:else}
			<p class="login-description">Redirecting to sign in...</p>
			<div class="loading-spinner"></div>
		{/if}

		<div class="login-actions">
			<button onclick={() => window.history.back()} class="btn btn-secondary">Go Back</button>
			<a href="/" class="btn btn-secondary">Go Home</a>
		</div>
	</div>
</div>

<style>
	.login-container {
		display: flex;
		align-items: center;
		justify-content: center;
		min-height: 70vh;
		padding: 2rem;
		text-align: center;
	}

	.login-content {
		max-width: 500px;
	}

	.login-icon {
		font-size: 4rem;
		margin-bottom: 1rem;
	}

	.login-title {
		font-size: 2rem;
		font-weight: 700;
		color: #333;
		margin: 0 0 1rem 0;
	}

	.login-description {
		font-size: 1.125rem;
		color: #666;
		line-height: 1.6;
		margin: 0 0 1rem 0;
	}

	.error-message {
		padding: 1rem;
		background-color: #ffebee;
		border-radius: 8px;
		color: #c62828;
		margin-bottom: 1rem;
	}

	.error-message p {
		margin: 0;
	}

	.login-hint {
		font-size: 0.9375rem;
		color: #888;
		line-height: 1.6;
		margin: 0 0 2rem 0;
		padding: 1rem;
		background-color: #f5f5f5;
		border-radius: 8px;
	}

	.loading-spinner {
		width: 40px;
		height: 40px;
		border: 4px solid #e0e0e0;
		border-top-color: #0066cc;
		border-radius: 50%;
		animation: spin 1s linear infinite;
		margin: 1rem auto 2rem;
	}

	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}

	.login-actions {
		display: flex;
		gap: 1rem;
		justify-content: center;
		flex-wrap: wrap;
	}

	.btn {
		display: inline-flex;
		align-items: center;
		padding: 0.75rem 1.5rem;
		font-size: 1rem;
		font-weight: 500;
		border-radius: 6px;
		text-decoration: none;
		cursor: pointer;
		transition: background-color 0.2s, transform 0.1s;
		border: none;
	}

	.btn:active {
		transform: scale(0.98);
	}

	.btn-secondary {
		background-color: #f5f5f5;
		color: #333;
		border: 1px solid #ddd;
	}

	.btn-secondary:hover {
		background-color: #e8e9ea;
	}

	@media (max-width: 768px) {
		.login-icon {
			font-size: 3rem;
		}

		.login-title {
			font-size: 1.5rem;
		}

		.login-actions {
			flex-direction: column;
		}

		.btn {
			width: 100%;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.loading-spinner {
			animation: none;
		}

		.btn {
			transition: none;
		}

		.btn:active {
			transform: none;
		}
	}
</style>
