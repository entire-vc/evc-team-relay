/**
 * GET /api/embed-token?slug=<share_slug>&path=<doc_path>
 *
 * Issues a short-lived (1h) signed embed URL for a private share doc.
 * Caller must provide a valid agent key in the Authorization header.
 *
 * The returned embed_url uses ?embed_token=<token> instead of ?agent_key=<key>,
 * so the long-lived key is never exposed to the browser.
 *
 * Required env vars:
 *   EMBED_TOKEN_SECRET  — HMAC-SHA256 signing secret (≥32 bytes random)
 *   PUBLIC_WEB_DOMAIN   — used to build the embed URL (e.g. docs.entire.vc)
 */

import { json, error } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { getShareBySlug } from '$lib/api';
import { signEmbedToken } from '$lib/embed-token';
import type { RequestHandler } from '@sveltejs/kit';

const EMBED_TTL_SECONDS = 3600; // 1 hour

export const GET: RequestHandler = async ({ url, request }) => {
	const secret = env.EMBED_TOKEN_SECRET;
	if (!secret || secret.length < 16) {
		throw error(503, 'Embed token feature not configured');
	}

	const slug = url.searchParams.get('slug');
	const path = url.searchParams.get('path') ?? '';

	if (!slug) {
		throw error(400, 'Missing required query param: slug');
	}

	// Validate caller's agent key
	const authHeader = request.headers.get('authorization') ?? '';
	const agentKey = authHeader.startsWith('Bearer ') ? authHeader.slice(7).trim() : '';
	if (!agentKey) {
		throw error(401, 'Authorization: Bearer <agent_key> required');
	}

	// Verify agent key is valid for this share by calling the control plane
	let share;
	try {
		share = await getShareBySlug(slug, agentKey);
	} catch {
		throw error(401, 'Invalid agent key or share not found');
	}

	if (share.visibility !== 'private') {
		// Non-private shares don't need token-based auth
		throw error(400, 'Embed tokens are only for private shares');
	}

	// Issue token
	const token = await signEmbedToken(slug, path, secret, EMBED_TTL_SECONDS);

	const webDomain = env.PUBLIC_WEB_DOMAIN ?? url.host;
	const protocol = webDomain.startsWith('localhost') ? 'http' : 'https';
	const pathSegment = path ? `/${path}` : '';
	const embedUrl = `${protocol}://${webDomain}/${slug}${pathSegment}?embed_token=${token}`;

	const expiresAt = new Date(Date.now() + EMBED_TTL_SECONDS * 1000).toISOString();

	return json({ embed_url: embedUrl, expires_at: expiresAt });
};
