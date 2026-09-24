import { defineConfig } from 'vitest/config';
import { sveltekit } from '@sveltejs/kit/vite';

export default defineConfig({
	plugins: [sveltekit()],
	test: {
		globals: true,
		setupFiles: [],
		projects: [
			{
				extends: true,
				test: {
					name: 'node',
					include: ['src/**/*.test.ts', 'src/tests/**/*.test.ts'],
					exclude: ['src/**/*.dom.test.ts', 'node_modules/**'],
					environment: 'node'
				}
			},
			{
				// Client-runtime tests: Svelte's `browser` export condition is what
				// makes mount()/effects real (the default `node` condition resolves
				// to the SSR build, where $effect never runs). Needed to pin
				// effect-graph bugs — e.g. an effect that reads and writes the same
				// $state — that SSR/`svelte/server` rendering cannot see.
				extends: true,
				resolve: { conditions: ['browser'] },
				test: {
					name: 'dom',
					include: ['src/**/*.dom.test.ts'],
					environment: 'jsdom'
				}
			}
		]
	}
});
