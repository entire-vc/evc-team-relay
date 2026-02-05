import { error } from '@sveltejs/kit';
import { getShareBySlug, validateSession, validateUserToken, getFolderFileContent } from '$lib/api';
import type { PageServerLoad } from './$types';

/**
 * Find README.md file in folder items (case-insensitive)
 * Only looks in root level (no slashes in path)
 */
function findReadme(items: { path: string; name: string; type: string }[]): string | null {
	for (const item of items) {
		// Check if it's a root-level doc with path matching readme.md (case-insensitive)
		if (item.type === 'doc' &&
			!item.path.includes('/') &&
			item.path.toLowerCase() === 'readme.md') {
			return item.path;
		}
	}
	return null;
}

export const load: PageServerLoad = async ({ params, cookies, url }) => {
	const { slug } = params;

	try {
		// Fetch share metadata from Control Plane
		const share = await getShareBySlug(slug);

		// For protected shares, check if user has valid session (password-based)
		let isAuthenticated = false;
		if (share.visibility === 'protected') {
			const sessionToken = cookies.get('web_session');
			if (sessionToken) {
				// Validate session with Control Plane
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
				// Validate JWT with Control Plane
				const validation = await validateUserToken(authToken);
				isOAuthAuthenticated = validation.valid;
				// TODO: Also check if user is owner/member of this share
			}
		}

		const needsPassword = share.visibility === 'protected' && !isAuthenticated;

		// For private shares without valid auth, redirect to login
		if (share.visibility === 'private' && !isOAuthAuthenticated) {
			throw error(
				401,
				'This share requires authentication. Please sign in to view it.'
			);
		}

		// Determine if user can edit (v1.8 web editing)
		// - Protected share: user has valid password session
		// - Private share: user is authenticated (TODO: check editor role)
		const canEdit =
			(share.visibility === 'protected' && isAuthenticated) ||
			(share.visibility === 'private' && isOAuthAuthenticated);

		// Handle folder vs document shares differently
		const isFolder = share.kind === 'folder';
		let content: string | null = null;
		let readmeContent: string | null = null;

		if (!isFolder) {
			// For document shares, use real content or placeholder
			if (share.web_content) {
				content = share.web_content;
			} else {
				content = `# ${share.path}

> **Content not yet synced**
>
> This document hasn't been synced from Obsidian yet. To publish content:
>
> 1. Open the Share Management in Obsidian
> 2. Click "Sync Now" to sync the document content
> 3. Refresh this page
`;
			}
		} else {
			// For folder shares, check for README.md in root
			const folderItems = share.web_folder_items || [];
			const readmePath = findReadme(folderItems);

			if (readmePath) {
				try {
					const sessionToken = share.visibility === 'protected' && isAuthenticated
						? cookies.get('web_session')
						: undefined;
					const fileData = await getFolderFileContent(slug, readmePath, sessionToken, authToken);
					readmeContent = fileData.content;
				} catch (err) {
					console.error('Failed to load README.md:', err);
					// Silently fail - will show default folder view
				}
			}
		}

		// Get session token for protected share real-time sync
		const sessionToken = share.visibility === 'protected' && isAuthenticated
			? cookies.get('web_session')
			: undefined;

		return {
			share,
			content,
			isFolder,
			folderItems: isFolder ? (share.web_folder_items || []) : [],
			readmeContent,
			needsPassword,
			sessionToken,
			authToken,
			canEdit
		};
	} catch (err) {
		// Re-throw SvelteKit errors (like our 401) as-is
		if (err && typeof err === 'object' && 'status' in err) {
			throw err;
		}
		console.error('Failed to load share:', err);
		throw error(404, 'Share not found or not published');
	}
};
