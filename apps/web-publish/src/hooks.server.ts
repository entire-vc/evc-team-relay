/**
 * Server-side hooks for SvelteKit.
 *
 * This file adds security headers to all responses and can be extended
 * for other server-side middleware needs.
 */

import type { Handle } from '@sveltejs/kit';

/**
 * Add security headers to all responses.
 */
export const handle: Handle = async ({ event, resolve }) => {
	const response = await resolve(event);

	// Security headers
	response.headers.set('X-Frame-Options', 'DENY');
	response.headers.set('X-Content-Type-Options', 'nosniff');
	response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');

	// Content Security Policy
	// Note: This is a restrictive policy. Adjust based on actual needs.
	const csp = [
		"default-src 'self'",
		"script-src 'self' 'unsafe-inline'", // unsafe-inline needed for SvelteKit hydration
		"style-src 'self' 'unsafe-inline'", // unsafe-inline needed for component styles
		"img-src 'self' data: https:",
		"font-src 'self' data:",
		"connect-src 'self'",
		"frame-ancestors 'none'",
		"base-uri 'self'",
		"form-action 'self'"
	].join('; ');

	response.headers.set('Content-Security-Policy', csp);

	// Permissions Policy (formerly Feature-Policy)
	response.headers.set(
		'Permissions-Policy',
		'geolocation=(), microphone=(), camera=(), payment=(), usb=()'
	);

	return response;
};
