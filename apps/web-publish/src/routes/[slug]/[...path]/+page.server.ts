import { error } from '@sveltejs/kit';
import { getShareBySlug, validateSession, validateUserToken, getFolderFileContent } from '$lib/api';
import { slugifyPath } from '$lib/file-tree';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, cookies, url }) => {
	const { slug, path } = params;
	const agentKey = url.searchParams.get('agent_key') ?? undefined;

	try {
		// Fetch folder share metadata from Control Plane (agent_key forwarded so CP can expose content)
		const share = await getShareBySlug(slug, agentKey);

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

		// For private shares: agent_key auth — CP already validated; content non-null = accepted.
		let isAgentAuthenticated = false;
		if (share.visibility === 'private' && agentKey) {
			isAgentAuthenticated = share.web_folder_items !== null;
		}

		// For private shares: OAuth/JWT auth (only when no agent_key)
		let isOAuthAuthenticated = false;
		let authToken: string | undefined;
		if (share.visibility === 'private' && !isAgentAuthenticated) {
			authToken = cookies.get('auth_token');
			if (authToken) {
				const validation = await validateUserToken(authToken);
				isOAuthAuthenticated = validation.valid;
			}
		}

		if (share.visibility === 'protected' && !isAuthenticated) {
			throw error(401, 'Password required');
		}
		if (share.visibility === 'private' && !isAgentAuthenticated && !isOAuthAuthenticated) {
			throw error(401, 'Authentication required');
		}

		const folderItems = share.web_folder_items || [];
		const file = folderItems.find(item => item.path === path)
			|| folderItems.find(item => slugifyPath(item.path) === path);

		if (!file) {
			throw error(404, 'File not found in this folder');
		}

		const originalPath = file.path;

		let content: string;
		try {
			const fileContent = await getFolderFileContent(slug, originalPath, sessionToken, authToken, agentKey);
			content = fileContent.content || '# Content not available\n\nThis file has not been synced yet.';
		} catch (fetchError) {
			content = `# ${file.name}\n\n> **Content not yet synced**`;
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
		if (err && typeof err === 'object' && 'status' in err) throw err;
		console.error('Failed to load file:', err);
		throw error(404, 'File not found');
	}
};
