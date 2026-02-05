<script lang="ts">
	import { page } from '$app/stores';

	// Error details
	let errorCode = $derived($page.status || 500);
	let errorMessage = $derived($page.error?.message || 'An unexpected error occurred');

	// Build login URL with return path
	let loginUrl = $derived(`/login?return=${encodeURIComponent($page.url.pathname)}`);

	// Custom error messages
	const errorMessages: Record<number, { title: string; description: string; showSignIn?: boolean }> = {
		401: {
			title: 'Authentication Required',
			description: 'This document is private. Please sign in to view it.',
			showSignIn: true
		},
		404: {
			title: 'Page Not Found',
			description: "The page you're looking for doesn't exist or has been moved."
		},
		403: {
			title: 'Access Denied',
			description: "You don't have permission to access this resource."
		},
		500: {
			title: 'Internal Server Error',
			description: 'Something went wrong on our end. Please try again later.'
		},
		503: {
			title: 'Service Unavailable',
			description: 'The service is temporarily unavailable. Please try again later.'
		}
	};

	let customError = $derived(
		errorMessages[errorCode] || {
			title: `Error ${errorCode}`,
			description: errorMessage
		}
	);
</script>

<svelte:head>
	<title>{errorCode} - {customError.title} - Relay</title>
</svelte:head>

<div class="error-container">
	<div class="error-content">
		<div class="error-icon">
			{#if errorCode === 401}
				🔐
			{:else if errorCode === 404}
				🔍
			{:else if errorCode === 403}
				🔒
			{:else if errorCode === 500 || errorCode === 503}
				⚠️
			{:else}
				❌
			{/if}
		</div>

		<h1 class="error-code">{errorCode}</h1>
		<h2 class="error-title">{customError.title}</h2>
		<p class="error-description">{customError.description}</p>

		<div class="error-actions">
			{#if customError.showSignIn}
				<a href={loginUrl} class="btn btn-primary">Sign In</a>
				<a href="/" class="btn btn-secondary">Go Home</a>
			{:else}
				<a href="/" class="btn btn-primary">Go Home</a>
				<button onclick={() => window.history.back()} class="btn btn-secondary">Go Back</button>
			{/if}
		</div>
	</div>
</div>

<style>
	.error-container {
		display: flex;
		align-items: center;
		justify-content: center;
		min-height: 70vh;
		padding: 2rem;
		text-align: center;
	}

	.error-content {
		max-width: 600px;
	}

	.error-icon {
		font-size: 5rem;
		margin-bottom: 1rem;
		animation: bounce 2s ease-in-out infinite;
	}

	@keyframes bounce {
		0%,
		100% {
			transform: translateY(0);
		}
		50% {
			transform: translateY(-10px);
		}
	}

	.error-code {
		font-size: 4rem;
		font-weight: 700;
		color: #d32f2f;
		margin: 0 0 0.5rem 0;
	}

	.error-title {
		font-size: 1.75rem;
		font-weight: 600;
		color: #333;
		margin: 0 0 1rem 0;
	}

	.error-description {
		font-size: 1.125rem;
		color: #666;
		line-height: 1.6;
		margin: 0 0 2rem 0;
	}

	.error-actions {
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
		transition:
			background-color 0.2s,
			transform 0.1s;
		border: none;
	}

	.btn:active {
		transform: scale(0.98);
	}

	.btn-primary {
		background-color: #0066cc;
		color: white;
	}

	.btn-primary:hover {
		background-color: #0052a3;
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
		.error-icon {
			font-size: 3rem;
		}

		.error-code {
			font-size: 3rem;
		}

		.error-title {
			font-size: 1.5rem;
		}

		.error-description {
			font-size: 1rem;
		}

		.error-actions {
			flex-direction: column;
		}

		.btn {
			width: 100%;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.error-icon {
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
