import { describe, it, expect } from 'vitest';
import { slugifyPath, hyphenatePath } from './file-tree.js';

// ---------------------------------------------------------------------------
// slugifyPath / hyphenatePath (#546ce7e3)
// ---------------------------------------------------------------------------
// Path validation on the control-plane deliberately allows real vault
// filenames through unencoded (parens, &, +, %, #, em dash, emoji — see
// app.core.path_validation). slugifyPath is where that content becomes a
// WORKING url; hyphenatePath is the plain (unencoded) form used to match an
// already-decoded incoming route param back to the original item.
// ---------------------------------------------------------------------------

describe('hyphenatePath', () => {
	it('replaces spaces with hyphens per segment, no encoding', () => {
		expect(hyphenatePath('My Folder/My File.md')).toBe('My-Folder/My-File.md');
	});

	it('leaves punctuation untouched', () => {
		expect(hyphenatePath('note (relay conflict 2026-09-05T20-25-23-268Z).md')).toBe(
			'note-(relay-conflict-2026-09-05T20-25-23-268Z).md'
		);
	});
});

describe('slugifyPath', () => {
	it('hyphenates then percent-encodes each segment', () => {
		expect(slugifyPath('My Folder/My File.md')).toBe('My-Folder/My-File.md');
		expect(slugifyPath('50% done.md')).toBe('50%25-done.md');
		expect(slugifyPath('Q&A #1.md')).toBe('Q%26A-%231.md');
	});

	it('keeps "/" as the segment separator instead of encoding it', () => {
		const result = slugifyPath('sub folder/note (2026).md');
		expect(result.split('/')).toHaveLength(2);
		expect(result).not.toContain('%2F');
	});

	it('round-trips through decodeURIComponent back to the hyphenated (not original) form', () => {
		const original = "it's mine & yours (v2).md";
		const encoded = slugifyPath(original);
		const decoded = encoded
			.split('/')
			.map((s) => decodeURIComponent(s))
			.join('/');
		// decodeURIComponent undoes the percent-encoding step; it does NOT
		// undo the earlier space->hyphen step, which is lossy by design.
		expect(decoded).toBe(hyphenatePath(original));
	});
});
