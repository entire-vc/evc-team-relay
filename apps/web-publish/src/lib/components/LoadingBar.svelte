<script lang="ts">
	import { navigating } from '$app/stores';

	// Progress state
	let progress = $state(0);
	let isNavigating = $state(false);
	let interval: ReturnType<typeof setInterval> | null = null;

	// Watch navigation state
	$effect(() => {
		if ($navigating) {
			// Start loading
			isNavigating = true;
			progress = 0;

			// Simulate progress
			interval = setInterval(() => {
				if (progress < 90) {
					progress += Math.random() * 10;
				}
			}, 200);
		} else {
			// Finish loading
			if (interval) {
				clearInterval(interval);
				interval = null;
			}
			progress = 100;

			// Hide after animation
			setTimeout(() => {
				isNavigating = false;
				progress = 0;
			}, 400);
		}

		// Cleanup
		return () => {
			if (interval) {
				clearInterval(interval);
			}
		};
	});
</script>

{#if isNavigating}
	<div class="loading-bar" style="width: {progress}%"></div>
{/if}

<style>
	.loading-bar {
		position: fixed;
		top: 0;
		left: 0;
		height: 3px;
		background: linear-gradient(90deg, #0066cc, #0099ff);
		transition: width 0.2s ease-out;
		z-index: 9999;
		box-shadow: 0 0 10px rgba(0, 102, 204, 0.5);
	}

	@media (prefers-reduced-motion: reduce) {
		.loading-bar {
			transition: none;
		}
	}
</style>
