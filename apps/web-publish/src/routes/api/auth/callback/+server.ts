import { redirect } from '@sveltejs/kit';
import { exchangeOAuthCode, getServerInfo } from '$lib/api';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ url, cookies }) => {
	const code = url.searchParams.get('code');
	const state = url.searchParams.get('state');
	const error = url.searchParams.get('error');
	const errorDescription = url.searchParams.get('error_description');

	// Get stored state and return URL
	const storedState = cookies.get('oauth_state');
	const returnTo = cookies.get('oauth_return') || '/';

	// Clear OAuth cookies
	cookies.delete('oauth_state', { path: '/' });
	cookies.delete('oauth_return', { path: '/' });

	// Handle OAuth errors
	if (error) {
		console.error('OAuth error:', error, errorDescription);
		return new Response(null, {
			status: 302,
			headers: {
				Location: `/login?error=${encodeURIComponent(errorDescription || error)}`
			}
		});
	}

	// Validate required params
	if (!code || !state) {
		return new Response(null, {
			status: 302,
			headers: {
				Location: '/login?error=Missing+authorization+code+or+state'
			}
		});
	}

	// Verify state matches (CSRF protection)
	if (state !== storedState) {
		console.error('OAuth state mismatch:', { received: state, stored: storedState });
		return new Response(null, {
			status: 302,
			headers: {
				Location: '/login?error=Invalid+state+parameter'
			}
		});
	}

	try {
		// Get OAuth provider name
		const serverInfo = await getServerInfo();
		const provider = serverInfo.features.oauth_provider;

		if (!provider) {
			throw new Error('OAuth provider not configured');
		}

		// Exchange code for token
		const tokenResponse = await exchangeOAuthCode(provider, code, state);

		// Set auth token cookie
		cookies.set('auth_token', tokenResponse.access_token, {
			path: '/',
			httpOnly: true,
			secure: url.protocol === 'https:',
			sameSite: 'lax',
			maxAge: tokenResponse.expires_in || 86400 // Default 24 hours
		});

		// Redirect to return URL
		return new Response(null, {
			status: 302,
			headers: {
				Location: returnTo
			}
		});
	} catch (err) {
		console.error('OAuth callback error:', err);
		const errorMessage = err instanceof Error ? err.message : 'Authentication failed';
		return new Response(null, {
			status: 302,
			headers: {
				Location: `/login?error=${encodeURIComponent(errorMessage)}`
			}
		});
	}
};
