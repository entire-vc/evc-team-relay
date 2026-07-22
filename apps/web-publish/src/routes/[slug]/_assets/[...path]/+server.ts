import type { RequestHandler } from './$types';

const CONTROL_PLANE_URL = process.env.CONTROL_PLANE_URL || 'http://control-plane:8000';

export const GET: RequestHandler = async ({ params, request, url }) => {
	const { slug, path } = params;

	// Forward cookies for auth (protected/private shares)
	const headers: Record<string, string> = {};
	const cookie = request.headers.get('cookie');
	if (cookie) headers['Cookie'] = cookie;
	const auth = request.headers.get('authorization');
	if (auth) headers['Authorization'] = auth;
	// Forward agent_key query param for private share auth
	const agentKey = url.searchParams.get('agent_key');
	if (agentKey) headers['X-Agent-Key'] = agentKey;

	const response = await fetch(
		`${CONTROL_PLANE_URL}/v1/web/shares/${slug}/assets?path=${encodeURIComponent(path)}`,
		{ headers }
	);

	if (!response.ok) {
		return new Response('Asset not found', { status: 404 });
	}

	const body = await response.arrayBuffer();
	const contentType = response.headers.get('content-type') || 'application/octet-stream';

	// TR-60: don't hardcode a cache policy here — control-plane already sets
	// Cache-Control based on the share's actual visibility (public shares get
	// `public, max-age=...`; private/protected shares get none at all, see
	// web.py's serve_web_asset). Forward whatever it decided instead of
	// overriding it with a blanket `public`, which would let any downstream
	// CDN/shared cache in front of this proxy serve a private asset to
	// anyone who guesses its URL.
	const responseHeaders: Record<string, string> = { 'Content-Type': contentType };
	const upstreamCacheControl = response.headers.get('cache-control');
	if (upstreamCacheControl) {
		responseHeaders['Cache-Control'] = upstreamCacheControl;
	}

	return new Response(body, { headers: responseHeaders });
};
