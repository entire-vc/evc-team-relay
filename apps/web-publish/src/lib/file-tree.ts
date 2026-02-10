/**
 * File tree utilities for WebPublish sidebar.
 * Transforms flat folder items into a hierarchical tree structure.
 */

import { browser } from '$app/environment';

export interface FolderItem {
	path: string;
	name: string;
	type: string;
}

export interface TreeNode {
	name: string;
	path: string;
	type: 'folder' | 'doc' | 'canvas';
	children: TreeNode[];
}

/**
 * Build a hierarchical tree from a flat list of folder items.
 * Handles nested paths like "folder1/subfolder/file.md".
 */
export function buildFileTree(items: FolderItem[]): TreeNode[] {
	const root: TreeNode[] = [];
	const nodeMap = new Map<string, TreeNode>();

	// Sort items to ensure folders are processed before their contents
	const sortedItems = [...items].sort((a, b) => {
		// Folders first, then by path
		if (a.type === 'folder' && b.type !== 'folder') return -1;
		if (a.type !== 'folder' && b.type === 'folder') return 1;
		return a.path.localeCompare(b.path);
	});

	for (const item of sortedItems) {
		const parts = item.path.split('/');
		const node: TreeNode = {
			name: item.name,
			path: item.path,
			type: item.type as 'folder' | 'doc' | 'canvas',
			children: []
		};

		if (parts.length === 1) {
			// Top-level item
			root.push(node);
			nodeMap.set(item.path, node);
		} else {
			// Nested item - find or create parent folders
			let currentPath = '';
			let parentChildren = root;

			for (let i = 0; i < parts.length - 1; i++) {
				currentPath = currentPath ? `${currentPath}/${parts[i]}` : parts[i];

				let parentNode = nodeMap.get(currentPath);
				if (!parentNode) {
					// Create implicit folder node
					parentNode = {
						name: parts[i],
						path: currentPath,
						type: 'folder',
						children: []
					};
					parentChildren.push(parentNode);
					nodeMap.set(currentPath, parentNode);
				}
				parentChildren = parentNode.children;
			}

			parentChildren.push(node);
			nodeMap.set(item.path, node);
		}
	}

	// Sort children: folders first, then alphabetically
	sortTreeNodes(root);

	return root;
}

/**
 * Recursively sort tree nodes: folders first, then alphabetically by name.
 */
function sortTreeNodes(nodes: TreeNode[]): void {
	nodes.sort((a, b) => {
		// Folders first
		if (a.type === 'folder' && b.type !== 'folder') return -1;
		if (a.type !== 'folder' && b.type === 'folder') return 1;
		// Then alphabetically
		return a.name.localeCompare(b.name);
	});

	for (const node of nodes) {
		if (node.children.length > 0) {
			sortTreeNodes(node.children);
		}
	}
}

/**
 * Get localStorage key for expanded state.
 */
function getStorageKey(slug: string): string {
	return `filetree-expanded-${slug}`;
}

/**
 * Save expanded folder paths to localStorage.
 */
export function saveExpandedState(slug: string, expandedPaths: Set<string>): void {
	if (!browser) return;

	try {
		const data = JSON.stringify(Array.from(expandedPaths));
		localStorage.setItem(getStorageKey(slug), data);
	} catch (e) {
		console.warn('Failed to save file tree state:', e);
	}
}

/**
 * Load expanded folder paths from localStorage.
 */
export function loadExpandedState(slug: string): Set<string> {
	if (!browser) return new Set();

	try {
		const data = localStorage.getItem(getStorageKey(slug));
		if (data) {
			const paths = JSON.parse(data) as string[];
			return new Set(paths);
		}
	} catch (e) {
		console.warn('Failed to load file tree state:', e);
	}

	return new Set();
}

/**
 * Slugify a file path for clean URLs: replace spaces with hyphens in each segment.
 * Example: "My Folder/My File.md" → "My-Folder/My-File.md"
 */
export function slugifyPath(path: string): string {
	return path
		.split('/')
		.map((s) => s.replace(/ /g, '-'))
		.join('/');
}

/**
 * Check if a node has any children (for determining if it's expandable).
 */
export function hasChildren(node: TreeNode): boolean {
	return node.children.length > 0;
}

/**
 * Get all folder paths from the tree (for "expand all" functionality).
 */
export function getAllFolderPaths(nodes: TreeNode[]): string[] {
	const paths: string[] = [];

	function traverse(node: TreeNode) {
		if (node.type === 'folder') {
			paths.push(node.path);
		}
		for (const child of node.children) {
			traverse(child);
		}
	}

	for (const node of nodes) {
		traverse(node);
	}

	return paths;
}
