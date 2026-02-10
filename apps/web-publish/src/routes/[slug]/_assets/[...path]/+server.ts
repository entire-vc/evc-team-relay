import type { RequestHandler } from './$types';

const CONTROL_PLANE_URL = process.env.CONTROL_PLANE_URL || 'http://control-plane:8000';

export const GET: RequestHandler = async ({ params, request }) => {
	const { slug, path } = params;

	// Forward cookies for auth (protected/private shares)
	const headers: Record<string, string> = {};
	const cookie = request.headers.get('cookie');
	if (cookie) headers['Cookie'] = cookie;
	const auth = request.headers.get('authorization');
	if (auth) headers['Authorization'] = auth;

	const response = await fetch(
		`${CONTROL_PLANE_URL}/v1/web/shares/${slug}/assets?path=${encodeURIComponent(path)}`,
		{ headers }
	);

	if (!response.ok) {
		return new Response('Asset not found', { status: 404 });
	}

	const body = await response.arrayBuffer();
	const contentType = response.headers.get('content-type') || 'application/octet-stream';

	return new Response(body, {
		headers: {
			'Content-Type': contentType,
			'Cache-Control': 'public, max-age=86400'
		}
	});
};
