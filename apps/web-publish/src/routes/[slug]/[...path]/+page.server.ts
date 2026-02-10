import { error } from '@sveltejs/kit';
import { getShareBySlug, validateSession, validateUserToken, getFolderFileContent } from '$lib/api';
import { slugifyPath } from '$lib/file-tree';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, cookies, url }) => {
	const { slug, path } = params;

	try {
		// Fetch folder share metadata from Control Plane
		const share = await getShareBySlug(slug);

		// Must be a folder share
		if (share.kind !== 'folder') {
			throw error(404, 'Not a folder share');
		}

		// For protected shares, check if user has valid session (password-based)
		let isAuthenticated = false;
		let sessionToken: string | undefined;
		if (share.visibility === 'protected') {
			sessionToken = cookies.get('web_session');
			if (sessionToken) {
				const validation = await validateSession(slug, sessionToken);
				isAuthenticated = validation.valid;
			}
		}

		// For private shares, check OAuth token
		let isOAuthAuthenticated = false;
		let authToken: string | undefined;
		if (share.visibility === 'private') {
			authToken = cookies.get('auth_token');
			if (authToken) {
				const validation = await validateUserToken(authToken);
				isOAuthAuthenticated = validation.valid;
			}
		}

		// Check authentication
		if (share.visibility === 'protected' && !isAuthenticated) {
			throw error(401, 'Password required');
		}
		if (share.visibility === 'private' && !isOAuthAuthenticated) {
			throw error(401, 'Authentication required');
		}

		// Find the file in folder items
		// Support both exact match and slugified match (spaces → hyphens in URL)
		const folderItems = share.web_folder_items || [];
		const file = folderItems.find(item => item.path === path)
			|| folderItems.find(item => slugifyPath(item.path) === path);

		if (!file) {
			throw error(404, 'File not found in this folder');
		}

		// Use original file.path (with spaces) for API calls
		const originalPath = file.path;

		// Try to fetch file content from API
		let content: string;
		try {
			const fileContent = await getFolderFileContent(slug, originalPath, sessionToken, authToken);
			content = fileContent.content || '# Content not available\n\nThis file has not been synced yet.';
		} catch (fetchError) {
			// If file content fetch fails, show placeholder
			content = `# ${file.name}

> **Content not yet synced**
>
> Individual document content within folder shares needs to be synced from Obsidian.
>
> To view this document:
> 1. Re-sync this folder share from the Obsidian plugin
> 2. Or create a separate share for this specific document
`;
		}

		return {
			share,
			file,
			content,
			filePath: slugifyPath(originalPath),
			parentSlug: slug,
			folderItems,
			isFolder: false
		};
	} catch (err) {
		// Re-throw SvelteKit errors as-is
		if (err && typeof err === 'object' && 'status' in err) {
			throw err;
		}
		console.error('Failed to load file:', err);
		throw error(404, 'File not found');
	}
};
