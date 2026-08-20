/**
 * Control Plane API client for web publishing.
 */

// H-M SSR timeouts: all control-plane fetches abort after 5 s to prevent SSR hangs.
const SSR_TIMEOUT_MS = 5000;

export async function fetchWithTimeout(
	url: string | URL,
	init: RequestInit = {},
	timeoutMs = SSR_TIMEOUT_MS
): Promise<Response> {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), timeoutMs);
	try {
		return await fetch(url, { ...init, signal: controller.signal });
	} finally {
		clearTimeout(timer);
	}
}

const CONTROL_PLANE_URL =
	typeof process !== 'undefined' && process.env.CONTROL_PLANE_URL
		? process.env.CONTROL_PLANE_URL
		: 'http://control-plane:8000';

/**
 * Thrown when the Control Plane confirms the share genuinely does not exist
 * (its 404). Distinct from network/timeout/5xx failures, which should be
 * surfaced to the reader as a transient outage, not a missing page.
 */
export class ShareNotFoundError extends Error {
	constructor(message = 'Share not found or not published') {
		super(message);
		this.name = 'ShareNotFoundError';
	}
}

export interface FolderItem {
	path: string;
	name: string;
	type: string;
}

export interface WebShare {
	id: string;
	kind: string;
	path: string;
	visibility: 'public' | 'protected' | 'private';
	web_slug: string;
	web_noindex: boolean;
	created_at: string;
	updated_at: string;
	web_content: string | null;
	web_content_updated_at: string | null;
	web_folder_items: FolderItem[] | null;
	web_doc_id: string | null; // Y-sweet document ID for real-time sync
}

export interface ShareAuthRequest {
	password: string;
}

export interface ShareAuthResponse {
	message: string;
	share_id: string;
}

export interface SessionValidation {
	valid: boolean;
	share_id: string | null;
}

/**
 * Fetch share metadata by slug.
 */
export async function getShareBySlug(slug: string, agentKey?: string): Promise<WebShare> {
	const urlObj = new URL(`${CONTROL_PLANE_URL}/v1/web/shares/${slug}`);
	// Agent key goes in the X-Agent-Key header, never the query string — a query
	// param leaks a write-scoped bearer secret into Caddy access logs, Referer
	// headers, and browser history (TR-14).
	const headers: Record<string, string> = {};
	if (agentKey) headers['X-Agent-Key'] = agentKey;
	const response = await fetchWithTimeout(urlObj.toString(), { headers });

	if (!response.ok) {
		if (response.status === 404) {
			throw new ShareNotFoundError();
		}
		throw new Error(`Failed to fetch share: ${response.statusText}`);
	}

	return response.json();
}

/**
 * Authenticate for a protected share using password.
 */
export async function authenticateShare(
	slug: string,
	password: string
): Promise<ShareAuthResponse> {
	const response = await fetchWithTimeout(`${CONTROL_PLANE_URL}/v1/web/shares/${slug}/auth`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({ password })
	});

	if (!response.ok) {
		if (response.status === 401) {
			throw new Error('Invalid password');
		}
		throw new Error(`Authentication failed: ${response.statusText}`);
	}

	return response.json();
}

/**
 * Validate a session token for a share.
 * Used server-side to check if user has access to protected share.
 */
export async function validateSession(slug: string, token: string): Promise<SessionValidation> {
	const response = await fetchWithTimeout(`${CONTROL_PLANE_URL}/v1/web/shares/${slug}/validate`, {
		headers: {
			Cookie: `web_session=${token}`
		}
	});

	if (!response.ok) {
		return { valid: false, share_id: null };
	}

	return response.json();
}

/**
 * Fetch robots.txt content from Control Plane.
 */
export async function getRobotsTxt(): Promise<string> {
	const response = await fetchWithTimeout(`${CONTROL_PLANE_URL}/v1/web/robots.txt`);

	if (!response.ok) {
		throw new Error(`Failed to fetch robots.txt: ${response.statusText}`);
	}

	return response.text();
}

/**
 * Fetch sitemap.xml content from Control Plane.
 */
export async function getSitemapXml(): Promise<string> {
	const response = await fetchWithTimeout(`${CONTROL_PLANE_URL}/v1/web/sitemap.xml`);

	if (!response.ok) {
		throw new Error(`Failed to fetch sitemap.xml: ${response.statusText}`);
	}

	return response.text();
}

export interface ServerInfo {
	id: string;
	name: string;
	version: string;
	relay_url: string;
	features: {
		multi_user: boolean;
		share_members: boolean;
		audit_logging: boolean;
		admin_ui: boolean;
		oauth_enabled?: boolean;
		oauth_provider?: string | null;
		web_publish_enabled?: boolean;
		web_publish_domain?: string | null;
	};
	branding: {
		name: string;
		logo_url: string;
		favicon_url: string;
		custom_head_code: string;
		custom_body_code: string;
	};
}

export interface OAuthAuthorizeResponse {
	authorize_url: string;
	state: string;
}

export interface OAuthCallbackResponse {
	access_token: string;
	token_type: string;
	expires_in: number;
	user: {
		id: string;
		email: string;
		name: string | null;
	};
}

/**
 * Get server info including OAuth configuration.
 */
export async function getServerInfo(): Promise<ServerInfo> {
	const response = await fetchWithTimeout(`${CONTROL_PLANE_URL}/server/info`);

	if (!response.ok) {
		throw new Error(`Failed to fetch server info: ${response.statusText}`);
	}

	return response.json();
}

/**
 * Get OAuth authorize URL from control plane.
 * Returns URL to redirect user to OAuth provider.
 */
export async function getOAuthAuthorizeUrl(
	provider: string,
	redirectUri: string
): Promise<OAuthAuthorizeResponse> {
	const response = await fetchWithTimeout(
		`${CONTROL_PLANE_URL}/v1/auth/oauth/${provider}/authorize?redirect_uri=${encodeURIComponent(redirectUri)}`,
		{
			headers: {
				Accept: 'application/json'
			}
		}
	);

	if (!response.ok) {
		throw new Error(`Failed to get OAuth authorize URL: ${response.statusText}`);
	}

	return response.json();
}

/**
 * Exchange OAuth code for tokens via control plane callback.
 */
export async function exchangeOAuthCode(
	provider: string,
	code: string,
	state: string
): Promise<OAuthCallbackResponse> {
	const response = await fetchWithTimeout(
		`${CONTROL_PLANE_URL}/v1/auth/oauth/${provider}/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}`
	);

	if (!response.ok) {
		const error = await response.json().catch(() => ({ detail: 'OAuth callback failed' }));
		throw new Error(error.detail || 'OAuth callback failed');
	}

	return response.json();
}

/**
 * Validate user JWT token with control plane.
 */
export async function validateUserToken(token: string): Promise<{ valid: boolean; user_id?: string }> {
	const response = await fetchWithTimeout(`${CONTROL_PLANE_URL}/v1/auth/me`, {
		headers: {
			Authorization: `Bearer ${token}`
		}
	});

	if (!response.ok) {
		return { valid: false };
	}

	const user = await response.json();
	return { valid: true, user_id: user.id };
}

export interface FolderFileContent {
	path: string;
	name: string;
	type: string;
	content: string;
}

/**
 * Get file content from folder share.
 */
export async function getFolderFileContent(
	slug: string,
	path: string,
	sessionToken?: string,
	authToken?: string,
	agentKey?: string
): Promise<FolderFileContent> {
	const headers: Record<string, string> = {};
	if (sessionToken) {
		headers['Cookie'] = `web_session=${sessionToken}`;
	}
	if (authToken) {
		headers['Authorization'] = `Bearer ${authToken}`;
	}
	// Agent key goes in the X-Agent-Key header, never the query string (TR-14).
	if (agentKey) {
		headers['X-Agent-Key'] = agentKey;
	}

	const urlObj = new URL(`${CONTROL_PLANE_URL}/v1/web/shares/${slug}/files`);
	urlObj.searchParams.set('path', path);

	const response = await fetchWithTimeout(urlObj.toString(), { headers });

	if (!response.ok) {
		throw new Error(`Failed to fetch file content: ${response.statusText}`);
	}

	return response.json();
}

/**
 * Update document content (for editing).
 */
export async function updateShareContent(
	slug: string,
	content: string,
	sessionToken?: string,
	authToken?: string
): Promise<{ message: string; updated_at: string }> {
	const headers: Record<string, string> = {
		'Content-Type': 'application/json'
	};

	if (sessionToken) {
		headers['Cookie'] = `web_session=${sessionToken}`;
	}
	if (authToken) {
		headers['Authorization'] = `Bearer ${authToken}`;
	}

	const response = await fetchWithTimeout(`${CONTROL_PLANE_URL}/v1/web/shares/${slug}/content`, {
		method: 'PUT',
		headers,
		body: JSON.stringify({ content })
	});

	if (!response.ok) {
		const error = await response.json().catch(() => ({ detail: 'Failed to update content' }));
		throw new Error(error.detail || 'Failed to update content');
	}

	return response.json();
}
