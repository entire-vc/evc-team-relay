/**
 * API endpoint for authenticating protected shares.
 *
 * This endpoint proxies authentication requests to the Control Plane
 * and forwards the Set-Cookie header to the client.
 */

import { json, error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

const CONTROL_PLANE_URL =
	typeof process !== 'undefined' && process.env.CONTROL_PLANE_URL
		? process.env.CONTROL_PLANE_URL
		: 'http://control-plane:8000';

interface AuthRequest {
	slug: string;
	password: string;
}

export const POST: RequestHandler = async ({ request, cookies }) => {
	let body: AuthRequest;

	try {
		body = await request.json();
	} catch (err) {
		throw error(400, 'Invalid request body');
	}

	const { slug, password } = body;

	if (!slug || !password) {
		throw error(400, 'Missing required fields: slug and password');
	}

	try {
		// Forward authentication request to Control Plane
		const response = await fetch(`${CONTROL_PLANE_URL}/v1/web/shares/${slug}/auth`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				// Forward client IP for rate limiting
				'X-Forwarded-For': request.headers.get('x-forwarded-for') || '',
				'X-Real-IP': request.headers.get('x-real-ip') || ''
			},
			body: JSON.stringify({ password })
		});

		if (!response.ok) {
			if (response.status === 401) {
				throw error(401, 'Invalid password');
			}
			if (response.status === 429) {
				throw error(429, 'Too many attempts. Please try again later.');
			}
			throw error(response.status, 'Authentication failed');
		}

		// Extract Set-Cookie header from Control Plane response
		const setCookieHeader = response.headers.get('set-cookie');

		if (setCookieHeader) {
			// Parse the cookie and set it using SvelteKit's cookies API
			// The Control Plane sets: web_session={token}; HttpOnly; Secure; SameSite=Strict; Max-Age=86400
			const cookieMatch = setCookieHeader.match(/web_session=([^;]+)/);

			if (cookieMatch) {
				const sessionToken = cookieMatch[1];

				// Set the cookie using SvelteKit's API (mirrors Control Plane settings)
				cookies.set('web_session', sessionToken, {
					path: '/',
					maxAge: 86400, // 24 hours
					httpOnly: true,
					secure: true,
					sameSite: 'strict'
				});
			}
		}

		const data = await response.json();

		return json({
			success: true,
			message: data.message,
			share_id: data.share_id
		});

	} catch (err) {
		// Re-throw SvelteKit errors
		if (err instanceof Error && 'status' in err) {
			throw err;
		}

		console.error('Authentication error:', err);
		throw error(500, 'Internal server error');
	}
};
