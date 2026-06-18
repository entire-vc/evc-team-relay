/**
 * Short-lived HMAC-signed embed tokens for iframe previews.
 *
 * Token format: base64url(payload) + "." + base64url(HMAC-SHA256)
 * Payload: JSON { slug, path, exp }
 *
 * Uses Web Crypto API (available globally in Node 18+/browsers) — no imports needed.
 */

interface EmbedTokenPayload {
	slug: string;
	path: string;
	exp: number;
}

export interface VerifyResult {
	ok: boolean;
	slug?: string;
	path?: string;
}

function b64urlEncode(input: Uint8Array | string): string {
	const bytes =
		typeof input === 'string' ? new TextEncoder().encode(input) : input;
	let binary = '';
	bytes.forEach((b) => (binary += String.fromCharCode(b)));
	return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

function b64urlDecode(s: string): Uint8Array {
	const padded = s.replace(/-/g, '+').replace(/_/g, '/');
	const pad = (4 - (padded.length % 4)) % 4;
	return Uint8Array.from(atob(padded + '='.repeat(pad)), (c) => c.charCodeAt(0));
}

async function hmacSign(data: string, secret: string): Promise<Uint8Array> {
	const enc = new TextEncoder();
	const key = await crypto.subtle.importKey(
		'raw',
		enc.encode(secret),
		{ name: 'HMAC', hash: 'SHA-256' },
		false,
		['sign']
	);
	return new Uint8Array(await crypto.subtle.sign('HMAC', key, enc.encode(data)));
}

export async function signEmbedToken(
	slug: string,
	path: string,
	secret: string,
	ttlSeconds = 3600
): Promise<string> {
	const payload: EmbedTokenPayload = {
		slug,
		path,
		exp: Math.floor(Date.now() / 1000) + ttlSeconds
	};
	const encoded = b64urlEncode(JSON.stringify(payload));
	const sig = b64urlEncode(await hmacSign(encoded, secret));
	return `${encoded}.${sig}`;
}

export async function verifyEmbedToken(token: string, secret: string): Promise<VerifyResult> {
	const dot = token.lastIndexOf('.');
	if (dot < 1) return { ok: false };

	const encoded = token.slice(0, dot);
	const providedSig = token.slice(dot + 1);

	const expectedSig = b64urlEncode(await hmacSign(encoded, secret));

	// Constant-time comparison — XOR all chars, accumulate diff
	if (providedSig.length !== expectedSig.length) return { ok: false };
	let diff = 0;
	for (let i = 0; i < providedSig.length; i++) {
		diff |= providedSig.charCodeAt(i) ^ expectedSig.charCodeAt(i);
	}
	if (diff !== 0) return { ok: false };

	let payload: EmbedTokenPayload;
	try {
		payload = JSON.parse(new TextDecoder().decode(b64urlDecode(encoded)));
	} catch {
		return { ok: false };
	}

	if (!payload.slug || payload.path === undefined || !payload.exp) {
		return { ok: false };
	}
	if (Math.floor(Date.now() / 1000) > payload.exp) {
		return { ok: false };
	}

	return { ok: true, slug: payload.slug, path: payload.path };
}
