// ---------------------------------------------------------------------------
// FileTree — expanded-state effect must not loop (#cee667d8)
// ---------------------------------------------------------------------------
// The "load expanded state on mount" $effect read AND wrote `expandedPaths`.
// For a folder share whose tree has files but no sub-folders, the first-visit
// branch computed an empty Set, persisted `[]`, and the next run saw
// `saved.size === 0 && tree.length > 0` again — forever, until Svelte aborted
// with `effect_update_depth_exceeded` (a pageerror on every such page, plus
// ~1000 iterations of localStorage + Set churn on the main thread during
// hydration). SSR rendering can't see this (effects never run there), so this
// mounts the real client runtime under jsdom.
// ---------------------------------------------------------------------------

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import FileTree from '../lib/components/FileTree.svelte';

const FILES_ONLY = [
	{ path: 'README.md', name: 'README.md', type: 'doc' },
	{ path: 'notes.md', name: 'notes.md', type: 'doc' }
];

const WITH_FOLDER = [
	{ path: 'guides', name: 'guides', type: 'folder' },
	{ path: 'guides/intro.md', name: 'intro.md', type: 'doc' },
	{ path: 'README.md', name: 'README.md', type: 'doc' }
];

function mountTree(items: typeof FILES_ONLY, currentPath = '') {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = mount(FileTree, {
		target,
		props: { items, currentSlug: 'demo', currentPath }
	});
	return { target, component };
}

describe('FileTree expanded-state effect', () => {
	beforeEach(() => {
		localStorage.clear();
		document.body.innerHTML = '';
	});

	it('does not loop on a tree with files but no folders', () => {
		const setItem = vi.spyOn(Storage.prototype, 'setItem');
		const { component } = mountTree(FILES_ONLY);
		// effect_update_depth_exceeded is thrown out of flushSync when the effect
		// re-triggers itself; a healthy effect settles in a handful of runs.
		expect(() => flushSync()).not.toThrow();
		expect(setItem.mock.calls.length).toBeLessThan(5);
		unmount(component);
		setItem.mockRestore();
	});

	it('still expands all folders on the first visit', () => {
		const { component } = mountTree(WITH_FOLDER);
		flushSync();
		expect(JSON.parse(localStorage.getItem('filetree-expanded-demo')!)).toEqual(['guides']);
		unmount(component);
	});

	it('still expands ancestors of the current path when state was already saved', () => {
		localStorage.setItem('filetree-expanded-demo', JSON.stringify(['other']));
		const { component } = mountTree(WITH_FOLDER, 'guides/intro.md');
		flushSync();
		const persisted = JSON.parse(localStorage.getItem('filetree-expanded-demo')!) as string[];
		expect(persisted).toEqual(expect.arrayContaining(['other', 'guides']));
		unmount(component);
	});
});
