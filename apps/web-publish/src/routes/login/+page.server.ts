import { redirect } from '@sveltejs/kit';
import { getServerInfo, getOAuthAuthorizeUrl } from '$lib/api';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, cookies }) => {
	// Check if already logged in
	const existingToken = cookies.get('auth_token');
	if (existingToken) {
		// Redirect to return URL or home
		const returnTo = url.searchParams.get('return') || '/';
		throw redirect(302, returnTo);
	}

	try {
		// Get server info to check OAuth configuration
		const serverInfo = await getServerInfo();

		if (!serverInfo.features.oauth_enabled || !serverInfo.features.oauth_provider) {
			// OAuth not configured - show message
			return {
				oauthEnabled: false,
				error: 'OAuth authentication is not configured on this server.'
			};
		}

		const provider = serverInfo.features.oauth_provider;

		// Build callback URL for this web-publish instance
		const callbackUrl = `${url.origin}/api/auth/callback`;

		// Get authorize URL from control plane
		const authResponse = await getOAuthAuthorizeUrl(provider, callbackUrl);

		// Store state in cookie for callback verification
		cookies.set('oauth_state', authResponse.state, {
			path: '/',
			httpOnly: true,
			secure: url.protocol === 'https:',
			sameSite: 'lax',
			maxAge: 600 // 10 minutes
		});

		// Store return URL
		const returnTo = url.searchParams.get('return') || '/';
		cookies.set('oauth_return', returnTo, {
			path: '/',
			httpOnly: true,
			secure: url.protocol === 'https:',
			sameSite: 'lax',
			maxAge: 600
		});

		// Redirect to OAuth provider
		throw redirect(302, authResponse.authorize_url);
	} catch (err) {
		// If it's a redirect, re-throw it
		if (err && typeof err === 'object' && 'status' in err && err.status === 302) {
			throw err;
		}

		console.error('Login error:', err);
		return {
			oauthEnabled: false,
			error: err instanceof Error ? err.message : 'Failed to initiate login'
		};
	}
};
