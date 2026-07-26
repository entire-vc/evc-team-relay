import { error } from '@sveltejs/kit';
import { getShareBySlug, validateSession, validateUserToken, getFolderFileContent, ShareNotFoundError } from '$lib/api';
import { renderMarkdown } from '$lib/markdown';
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
	const agentKey = url.searchParams.get('agent_key') ?? undefined;

	try {
		// Fetch share metadata from Control Plane (agent_key forwarded so CP can expose content)
		const share = await getShareBySlug(slug, agentKey);

		// For protected shares, check if user has valid session (password-based)
		let isAuthenticated = false;
		if (share.visibility === 'protected') {
			const sessionToken = cookies.get('web_session');
			if (sessionToken) {
				const validation = await validateSession(slug, sessionToken);
				isAuthenticated = validation.valid;
			}
		}

		// For private shares: agent_key auth — CP already validated the key;
		// if content is non-null the key was accepted.
		let isAgentAuthenticated = false;
		if (share.visibility === 'private' && agentKey) {
			isAgentAuthenticated = share.web_content !== null || share.web_folder_items !== null;
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

		const needsPassword = share.visibility === 'protected' && !isAuthenticated;

		if (share.visibility === 'private' && !isAgentAuthenticated && !isOAuthAuthenticated) {
			throw error(401, 'This share requires authentication. Please sign in to view it.');
		}

		const canEdit =
			(share.visibility === 'protected' && isAuthenticated) ||
			(share.visibility === 'private' && (isOAuthAuthenticated || isAgentAuthenticated));

		const isFolder = share.kind === 'folder';
		let content: string | null = null;
		let readmeContent: string | null = null;
		const folderItems = isFolder ? (share.web_folder_items || []) : [];

		if (!isFolder) {
			content = share.web_content ?? `# ${share.path}\n\n> **Content not yet synced**\n>\n> Refresh after syncing from Obsidian.`;
		} else {
			const readmePath = findReadme(folderItems);

			if (readmePath) {
				try {
					const sessionToken = share.visibility === 'protected' && isAuthenticated
						? cookies.get('web_session')
						: undefined;
					const fileData = await getFolderFileContent(slug, readmePath, sessionToken, authToken, agentKey);
					readmeContent = fileData.content;
				} catch (err) {
					console.error('Failed to load README.md:', err);
				}
			}
		}

		// Render markdown to HTML server-side so the document body is present in the
		// SSR output (non-JS crawlers/AI scrapers/social unfurlers never run the
		// client-side render in MarkdownViewer's onMount) — TR-37.
		const contentHtml = content
			? await renderMarkdown(content, { slug: share.web_slug, folderItems })
			: null;
		const readmeHtml = readmeContent
			? await renderMarkdown(readmeContent, { slug: share.web_slug, folderItems })
			: null;

		const sessionToken = share.visibility === 'protected' && isAuthenticated
			? cookies.get('web_session')
			: undefined;

		return {
			share,
			content,
			contentHtml,
			isFolder,
			folderItems,
			readmeContent,
			readmeHtml,
			needsPassword,
			sessionToken,
			authToken,
			canEdit,
			agentKey
		};
	} catch (err) {
		if (err && typeof err === 'object' && 'status' in err) throw err;
		if (err instanceof ShareNotFoundError) {
			console.error('Share not found:', err);
			throw error(404, 'Share not found or not published');
		}
		console.error('Upstream error loading share:', err);
		throw error(503, 'Service temporarily unavailable. Please try again shortly.');
	}
};
