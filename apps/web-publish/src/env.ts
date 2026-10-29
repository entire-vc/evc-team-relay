import { defineEnvVars } from '@sveltejs/kit/env';

// Runtime (non-static) variables, read when the server starts. Every schema
// passes the raw value through, so an unset variable stays `undefined` — the
// same as `env.X` from the former `$env/dynamic/*` modules; call sites keep
// their own fallbacks (`?? url.host`, `if (!secret)`).
const optional = (input: string | undefined) => input;

export const variables = defineEnvVars({
	WEB_FRAME_ANCESTORS: { schema: optional },
	EMBED_TOKEN_SECRET: { schema: optional },
	PUBLIC_WEB_DOMAIN: { schema: optional },
	WEB_PUBLISH_AGENT_KEY: { schema: optional },
	PUBLIC_LAZY_RENDERERS_DISABLED: { public: true, schema: optional }
});
