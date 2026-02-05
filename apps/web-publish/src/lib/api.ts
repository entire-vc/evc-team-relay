/**
 * Control Plane API client for web publishing.
 */

const CONTROL_PLANE_URL =
	typeof process !== 'undefined' && process.env.CONTROL_PLANE_URL
		? process.env.CONTROL_PLANE_URL
		: 'http://control-plane:8000';

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

export interface RelayToken {
	relay_url: string;
	token: string;
	doc_id: string;
	expires_at: string;
}

/**
 * Fetch share metadata by slug.
 */
export async function getShareBySlug(slug: string): Promise<WebShare> {
	const response = await fetch(`${CONTROL_PLANE_URL}/v1/web/shares/${slug}`);

	if (!response.ok) {
		if (response.status === 404) {
			throw new Error('Share not found or not published');
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
	const response = await fetch(`${CONTROL_PLANE_URL}/v1/web/shares/${slug}/auth`, {
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
	const response = await fetch(`${CONTROL_PLANE_URL}/v1/web/shares/${slug}/validate`, {
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
	const response = await fetch(`${CONTROL_PLANE_URL}/v1/web/robots.txt`);

	if (!response.ok) {
		throw new Error(`Failed to fetch robots.txt: ${response.statusText}`);
	}

	return response.text();
}

/**
 * Get relay token for real-time sync.
 * Returns token data for connecting to y-sweet WebSocket.
 */
export async function getRelayToken(slug: string, sessionToken?: string): Promise<RelayToken> {
	const headers: Record<string, string> = {};
	if (sessionToken) {
		headers['Cookie'] = `web_session=${sessionToken}`;
	}

	const response = await fetch(`${CONTROL_PLANE_URL}/v1/web/shares/${slug}/token`, {
		headers
	});

	if (!response.ok) {
		if (response.status === 403) {
			throw new Error('Authentication required for real-time sync');
		}
		if (response.status === 404) {
			throw new Error('Real-time sync not available for this share');
		}
		throw new Error(`Failed to get relay token: ${response.statusText}`);
	}

	return response.json();
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
	const response = await fetch(`${CONTROL_PLANE_URL}/server/info`);

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
	const response = await fetch(
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
	const response = await fetch(
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
	const response = await fetch(`${CONTROL_PLANE_URL}/v1/auth/me`, {
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
	authToken?: string
): Promise<FolderFileContent> {
	const headers: Record<string, string> = {};
	if (sessionToken) {
		headers['Cookie'] = `web_session=${sessionToken}`;
	}
	if (authToken) {
		headers['Authorization'] = `Bearer ${authToken}`;
	}

	const response = await fetch(
		`${CONTROL_PLANE_URL}/v1/web/shares/${slug}/files?path=${encodeURIComponent(path)}`,
		{ headers }
	);

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

	const response = await fetch(`${CONTROL_PLANE_URL}/v1/web/shares/${slug}/content`, {
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
