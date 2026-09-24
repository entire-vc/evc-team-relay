import { env } from '$env/dynamic/private';
import type { Handle } from '@sveltejs/kit';

export const handle: Handle = async ({ event, resolve }) => {
	const response = await resolve(event);

	// X-Frame-Options omitted — frame-ancestors CSP is the modern replacement
	// and supports allowlists; X-Frame-Options only supports DENY/SAMEORIGIN.
	response.headers.set('X-Content-Type-Options', 'nosniff');
	response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');

	const frameAncestors = env.WEB_FRAME_ANCESTORS
		? `'self' ${env.WEB_FRAME_ANCESTORS}`
		: "'none'";

	const csp = [
		"default-src 'self'",
		"script-src 'self' 'unsafe-inline'",
		"style-src 'self' 'unsafe-inline'",
		"img-src 'self' data: https:",
		"font-src 'self' data:",
		"connect-src 'self'",
		`frame-ancestors ${frameAncestors}`,
		"base-uri 'self'",
		"form-action 'self'"
	].join('; ');

	// A route that sets its own (stricter) CSP, e.g. the logo proxy, keeps it.
	if (!response.headers.has('Content-Security-Policy')) {
		response.headers.set('Content-Security-Policy', csp);
	}
	response.headers.set(
		'Permissions-Policy',
		'geolocation=(), microphone=(), camera=(), payment=(), usb=()'
	);

	return response;
};
