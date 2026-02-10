<script lang="ts">
	import { page } from '$app/stores';

	// Get branding from layout data
	const branding = $derived($page.data?.serverInfo?.branding);
	const instanceName = $derived(branding?.name || 'Relay');
</script>

<svelte:head>
	<title>{instanceName} - Collaborative Document Sharing</title>
	<meta
		name="description"
		content="Share and collaborate on documents with your team using {instanceName}'s web publishing platform"
	/>
	<meta property="og:title" content="{instanceName} - Collaborative Document Sharing" />
	<meta property="og:description" content="Share and collaborate on documents with your team using {instanceName}'s web publishing platform" />
	<meta property="og:type" content="website" />
	{#if branding?.logo_url}
		<meta property="og:image" content={branding.logo_url} />
	{/if}
	<meta name="twitter:card" content="summary" />
	<meta name="twitter:title" content="{instanceName} - Collaborative Document Sharing" />
	<meta name="twitter:description" content="Share and collaborate on documents with your team using {instanceName}'s web publishing platform" />
	{#if branding?.logo_url}
		<meta name="twitter:image" content={branding.logo_url} />
	{/if}
</svelte:head>

<div class="home-container">
	<div class="hero-section">
		<div class="hero-content">
			<div class="logo-container">
				{#if branding?.logo_url}
					<img src={branding.logo_url} alt="{instanceName} logo" class="logo" />
				{:else}
					<svg class="logo" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
						<circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/>
						<path d="M12 6v12M6 12h12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
					</svg>
				{/if}
			</div>
			<h1 class="instance-name">{instanceName}</h1>
			<div class="footer-link">
				<a href="https://github.com/entire-vc" target="_blank" rel="noopener" class="powered-by">
					Entire VC Team Relay
				</a>
			</div>
		</div>
	</div>

</div>

<style>
	.home-container {
		width: 100%;
		height: 100%;
		display: flex;
		align-items: center;
		justify-content: center;
	}

	.hero-section {
		text-align: center;
		padding: 2rem;
	}

	.hero-content {
		max-width: 500px;
		margin: 0 auto;
	}

	.logo-container {
		display: flex;
		justify-content: center;
		margin-bottom: 2rem;
	}

	.logo {
		width: 120px;
		height: 120px;
		color: var(--muted-foreground);
		object-fit: contain;
	}

	.instance-name {
		font-size: 3rem;
		font-weight: 700;
		color: var(--foreground);
		margin: 0 0 2rem 0;
		letter-spacing: -0.02em;
	}

	.footer-link {
		margin-top: 3rem;
	}

	.powered-by {
		font-size: 0.875rem;
		color: var(--muted-foreground);
		text-decoration: none;
		transition: color 0.2s;
	}

	.powered-by:hover {
		color: var(--foreground);
	}

	/* Responsive */
	@media (max-width: 768px) {
		.logo {
			width: 80px;
			height: 80px;
		}

		.instance-name {
			font-size: 2rem;
		}
	}

	/* Reduce motion for accessibility */
	@media (prefers-reduced-motion: reduce) {
		.powered-by {
			transition: none;
		}
	}
</style>
