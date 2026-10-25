import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import postcss from 'postcss';
import tailwind from '@tailwindcss/postcss';
import { describe, expect, it } from 'vitest';

const appCssPath = resolve(process.cwd(), 'src/app.css');

async function compile(source: string): Promise<string> {
	const result = await postcss([tailwind()]).process(source, { from: appCssPath });
	return result.css;
}

describe('app.css compiled with Tailwind v4', () => {
	it('emits tokens, page utilities and the dark variant, with no raw directives left', async () => {
		// force-generate one dark: utility so the variant itself is exercised, not just the palette
		const css = await compile(
			readFileSync(appCssPath, 'utf8') + '\n@source inline("dark:text-foreground");\n'
		);

		// @theme tokens from @entire-vc/tokens survive as custom properties
		expect(css).toContain('--color-primary: hsl(var(--primary))');
		expect(css).toContain('--color-background: hsl(var(--background))');
		expect(css).toContain('--primary: 239 84% 67%');

		// utilities used by the login, home, share and protected-share pages
		for (const selector of [
			'.text-foreground',
			'.text-muted-foreground',
			'.text-primary',
			'.mx-auto',
			'.mb-4',
			'.gap-8',
			'.text-6xl',
			'.max-w-\\[800px\\]'
		]) {
			expect(css, selector).toContain(selector);
		}

		// component classes defined in app.css itself
		for (const selector of ['.sr-only', '.skip-link']) {
			expect(css, selector).toContain(selector);
		}

		// dark variant from the tokens' @custom-variant, plus the .dark palette
		expect(css).toMatch(/\.dark\b/);
		expect(css).toMatch(/\.dark\\:text-foreground:where\(\.dark, \.dark \*\)/);

		// nothing unprocessed reaches the browser
		expect(css).not.toMatch(/@tailwind\b/);
		expect(css).not.toMatch(/@import\s+["']tailwindcss/);
		expect(css).not.toMatch(/@import\s+["']@entire-vc/);
		expect(css).not.toMatch(/@theme\b/);
		expect(css).not.toMatch(/@source\b/);
		expect(css).not.toMatch(/@custom-variant\b/);
	});

	it('rejects CSS without the tokens import (red control)', async () => {
		const broken = readFileSync(appCssPath, 'utf8').replace(
			/@import "@entire-vc\/tokens[^\n]*\n/,
			''
		);
		// app.css uses `border-border` via @apply, so without the tokens the build throws
		await expect(compile(broken)).rejects.toThrow(/unknown utility class/);
	});
});
