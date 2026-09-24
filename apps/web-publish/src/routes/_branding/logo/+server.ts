import { fetchWithTimeout, getServerInfo } from '$lib/api';
import { proxyableLogoPath } from '$lib/branding';
import type { RequestHandler } from './$types';

const CONTROL_PLANE_URL = process.env.CONTROL_PLANE_URL || 'http://control-plane:8000';
const PUBLIC_CONTROL_PLANE_URL = process.env.PUBLIC_CONTROL_PLANE_URL || '';

// The logo changes about never, and the URL is not content-hashed, so keep the
// browser TTL modest; the in-process copy just avoids a control-plane round
// trip (server info + asset) on every home-page view.
const BROWSER_TTL_S = 3600;
const PROCESS_TTL_MS = 5 * 60 * 1000;
const MAX_LOGO_BYTES = 1024 * 1024;

let cached: { body: ArrayBuffer; contentType: string; at: number } | null = null;

function logoResponse(body: ArrayBuffer, contentType: string): Response {
	return new Response(body, {
		headers: {
			'content-type': contentType,
			'cache-control': `public, max-age=${BROWSER_TTL_S}`,
			// Defence in depth: whatever this is, it is only ever an <img> source. The
			// sandboxed, script-less policy holds even if the URL is opened directly
			// (hooks.server.ts leaves a route-set CSP alone).
			'content-security-policy': "default-src 'none'; sandbox"
		}
	});
}

export const GET: RequestHandler = async () => {
	if (cached && Date.now() - cached.at < PROCESS_TTL_MS) {
		return logoResponse(cached.body, cached.contentType);
	}

	let logoUrl: string | undefined;
	try {
		logoUrl = (await getServerInfo()).branding?.logo_url;
	} catch {
		return new Response('Upstream timeout', { status: 504 });
	}
	// Only a path on the control plane itself is proxied; a logo hosted anywhere
	// else (CDN) is rendered from its own URL by the page and never reaches this
	// route — so this can never be pointed at an arbitrary host.
	const logoPath = logoUrl ? proxyableLogoPath(logoUrl, PUBLIC_CONTROL_PLANE_URL) : null;
	if (!logoPath) {
		return new Response('No logo', { status: 404 });
	}

	let upstream: Response;
	try {
		// redirect: 'manual' — a redirect would leave the control-plane origin.
		upstream = await fetchWithTimeout(`${CONTROL_PLANE_URL}${logoPath}`, { redirect: 'manual' });
	} catch {
		return new Response('Upstream timeout', { status: 504 });
	}
	if (upstream.status >= 300 && upstream.status < 400) {
		return new Response('Upstream error', { status: 502 });
	}
	if (!upstream.ok) {
		return new Response('Logo not found', { status: upstream.status === 404 ? 404 : 502 });
	}

	const contentType = upstream.headers.get('content-type') || 'application/octet-stream';
	// Only ever serve a raster image from here — never reflect an arbitrary
	// upstream type, and never SVG (see proxyableLogoPath).
	if (!contentType.startsWith('image/') || contentType.toLowerCase().includes('svg')) {
		return new Response('Upstream error', { status: 502 });
	}
	if (Number(upstream.headers.get('content-length') ?? 0) > MAX_LOGO_BYTES) {
		return new Response('Upstream error', { status: 502 });
	}
	const body = await upstream.arrayBuffer();
	if (body.byteLength > MAX_LOGO_BYTES) {
		return new Response('Upstream error', { status: 502 });
	}

	cached = { body, contentType, at: Date.now() };
	return logoResponse(body, contentType);
};
