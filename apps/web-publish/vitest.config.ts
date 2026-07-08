import { defineConfig } from 'vitest/config';
import { sveltekit } from '@sveltejs/kit/vite';

export default defineConfig({
	plugins: [sveltekit()],
	test: {
		include: ['src/**/*.test.ts', 'src/tests/**/*.test.ts'],
		environment: 'node',
		globals: true,
		setupFiles: []
	}
});
