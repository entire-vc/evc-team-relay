import { error } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { getShareBySlug, validateSession, validateUserToken, getFolderFileContent, ShareNotFoundError } from '$lib/api';
import { slugifyPath } from '$lib/file-tree';
import { verifyEmbedToken } from '$lib/embed-token';
import { renderMarkdown } from '$lib/markdown';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, cookies, url }) => {
	const { slug, path } = params;
	const agentKey = url.searchParams.get('agent_key') ?? undefined;
	const embedToken = url.searchParams.get('embed_token') ?? undefined;

	// Determine the key to use for CP calls:
	// - embed_token path uses WEB_PUBLISH_AGENT_KEY (server-side only, never in browser)
	// - direct agent_key path uses the caller-provided key
	let resolvedAgentKey = agentKey;
	let isEmbedTokenAuth = false;

	if (embedToken && !agentKey) {
		const secret = env.EMBED_TOKEN_SECRET;
		if (secret) {
			const result = await verifyEmbedToken(embedToken, secret);
			if (result.ok && result.slug === slug) {
				const serviceKey = env.WEB_PUBLISH_AGENT_KEY;
				if (serviceKey) {
					resolvedAgentKey = serviceKey;
					isEmbedTokenAuth = true;
				}
			}
		}
	}

	try {
		// Fetch folder share metadata from Control Plane
		const share = await getShareBySlug(slug, resolvedAgentKey);

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

		// For private shares: embed_token auth (short-lived token issued by /api/embed-token)
		// For private shares: agent_key auth — CP already validated; content non-null = accepted.
		let isAgentAuthenticated = false;
		if (share.visibility === 'private' && resolvedAgentKey) {
			isAgentAuthenticated = share.web_folder_items !== null;
		}

		// For private shares: OAuth/JWT auth (only when no key-based auth)
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
			const fileContent = await getFolderFileContent(slug, originalPath, sessionToken, authToken, resolvedAgentKey);
			content = fileContent.content || '# Content not available\n\nThis file has not been synced yet.';
		} catch (fetchError) {
			content = `# ${file.name}\n\n> **Content not yet synced**`;
		}

		// Render markdown to HTML server-side so the document body is present in the
		// SSR output, not only after client-side hydration (TR-37).
		const contentHtml = await renderMarkdown(content, { slug: share.web_slug, folderItems });

		return {
			share,
			file,
			content,
			contentHtml,
			filePath: slugifyPath(originalPath),
			parentSlug: slug,
			folderItems,
			isFolder: false,
			// Never expose the resolved key or service key to the client
			agentKey: isEmbedTokenAuth ? undefined : agentKey,
		};
	} catch (err) {
		if (err && typeof err === 'object' && 'status' in err) throw err;
		if (err instanceof ShareNotFoundError) {
			console.error('Share not found:', err);
			throw error(404, 'File not found');
		}
		console.error('Upstream error loading file:', err);
		throw error(503, 'Service temporarily unavailable. Please try again shortly.');
	}
};
