/**
 * Same-origin logo delivery.
 *
 * The control plane serves the instance logo from its own origin. Rendered as
 * a cross-origin <img>, the first byte waits on DNS + TCP + TLS to that
 * origin — and on the home page the logo is the LCP element. Rendering it via
 * this app's origin reuses the connection the document already opened.
 */

export const LOGO_PROXY_PATH = '/_branding/logo';

/**
 * The control-plane path behind a branding URL, or null when the URL is not
 * ours to proxy. Accepts a relative path ("/static/img/logo.png") or an
 * absolute URL on the control plane's own public origin — which is what
 * /server/info actually returns in production.
 */
export function controlPlaneAssetPath(url: string, publicBase: string): string | null {
	if (isControlPlanePath(url)) return url;
	if (!publicBase) return null;
	try {
		const target = new URL(url);
		if (target.origin !== new URL(publicBase).origin) return null;
		const path = target.pathname + target.search;
		return isControlPlanePath(path) ? path : null;
	} catch {
		return null;
	}
}

/**
 * The control-plane path to proxy for the instance logo, or null. Like
 * controlPlaneAssetPath, but never SVG: served from our origin, an SVG opened
 * as a document can run script (the CSP allows 'unsafe-inline'), and it would
 * do so on the origin that holds share session cookies. An SVG logo keeps
 * rendering straight from its own URL, where <img> never executes it.
 */
export function proxyableLogoPath(url: string, publicBase: string): string | null {
	const path = controlPlaneAssetPath(url, publicBase);
	if (!path) return null;
	const pathname = path.split(/[?#]/)[0].toLowerCase();
	return pathname.endsWith('.svg') || pathname.endsWith('.svgz') ? null : path;
}

/**
 * True for a control-plane-relative asset path ("/static/img/logo.png").
 * Rejects protocol-relative ("//host/x") and traversal forms so the proxy can
 * only ever fetch a path under the control-plane origin itself.
 */
export function isControlPlanePath(value: string): boolean {
	return (
		value.startsWith('/') &&
		!value.startsWith('//') &&
		!value.includes('\\') &&
		!value.split('/').includes('..')
	);
}
