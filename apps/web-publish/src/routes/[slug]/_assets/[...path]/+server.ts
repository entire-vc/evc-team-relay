import { fetchWithTimeout } from '$lib/api';
import type { RequestHandler } from './$types';

const CONTROL_PLANE_URL = process.env.CONTROL_PLANE_URL || 'http://control-plane:8000';

// #d0e32ac0: map the upstream status instead of collapsing everything into a
// blanket 404 — but NEVER forward the upstream response body on an error
// branch. control-plane's 500 body carries a raw S3Error (bucket name,
// object key); the old blanket-404 accidentally hid that leak, a naive
// pass-through would have exposed it. Status is proxied, body is always ours.
function upstreamErrorResponse(status: number): Response {
	if (status === 401) {
		// No WWW-Authenticate: this URL can be loaded from a bare <img src>,
		// and that header triggers a native basic-auth browser dialog — we
		// use cookie/OAuth, not basic auth, so the dialog can't do anything.
		return new Response('Authentication required', { status: 401 });
	}
	if (status === 403) {
		return new Response('Forbidden', { status: 403 });
	}
	if (status === 404) {
		return new Response('Asset not found', { status: 404 });
	}
	// 5xx (and any other unexpected upstream status) → 502: the proxy itself
	// is fine, the upstream failed. A bare 500 here would read as web-publish
	// itself being down rather than control-plane/MinIO.
	return new Response('Upstream error', { status: 502 });
}

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

	let response: Response;
	try {
		response = await fetchWithTimeout(
			`${CONTROL_PLANE_URL}/v1/web/shares/${slug}/assets?path=${encodeURIComponent(path)}`,
			{ headers }
		);
	} catch {
		// Network error or abort/timeout — control-plane never answered at
		// all, distinct from it answering with a 5xx.
		return new Response('Upstream timeout', { status: 504 });
	}

	if (!response.ok) {
		return upstreamErrorResponse(response.status);
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
