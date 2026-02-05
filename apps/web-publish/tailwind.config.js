import evcPreset from '@evc/tokens/tailwind-preset';

/** @type {import('tailwindcss').Config} */
export default {
	presets: [evcPreset],
	content: [
		'./src/**/*.{html,js,svelte,ts}',
		'./vendor/@evc/ui-svelte/dist/**/*.{js,svelte}'
	],
	darkMode: 'class'
};
