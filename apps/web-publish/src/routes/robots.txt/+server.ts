import { getRobotsTxt } from '$lib/api';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async () => {
	try {
		const content = await getRobotsTxt();

		return new Response(content, {
			headers: {
				'Content-Type': 'text/plain; charset=utf-8',
				'Cache-Control': 'public, max-age=3600' // Cache for 1 hour
			}
		});
	} catch (err) {
		console.error('Failed to fetch robots.txt:', err);

		// Fallback to safe default (disallow all)
		const fallback = 'User-agent: *\nDisallow: /\n';

		return new Response(fallback, {
			headers: {
				'Content-Type': 'text/plain; charset=utf-8'
			}
		});
	}
};
