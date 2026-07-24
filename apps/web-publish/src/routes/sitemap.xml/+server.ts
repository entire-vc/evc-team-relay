import { getSitemapXml } from '$lib/api';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async () => {
	try {
		const content = await getSitemapXml();

		return new Response(content, {
			headers: {
				'Content-Type': 'application/xml',
				'Cache-Control': 'public, max-age=3600' // Cache for 1 hour
			}
		});
	} catch (err) {
		console.error('Failed to fetch sitemap.xml:', err);

		// Fallback to a valid empty sitemap rather than an error page
		const fallback =
			'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>\n';

		return new Response(fallback, {
			headers: {
				'Content-Type': 'application/xml'
			}
		});
	}
};
