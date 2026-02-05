import type { LayoutServerLoad } from './$types';
import { getServerInfo } from '$lib/api';

// Public URL for control plane (for branding assets)
const PUBLIC_CONTROL_PLANE_URL = process.env.PUBLIC_CONTROL_PLANE_URL || '';

export const load: LayoutServerLoad = async ({ url }) => {
	// Fetch server info for branding
	const serverInfo = await getServerInfo();

	// Convert relative branding URLs to absolute using public control plane URL
	if (serverInfo?.branding && PUBLIC_CONTROL_PLANE_URL) {
		if (serverInfo.branding.logo_url?.startsWith('/')) {
			serverInfo.branding.logo_url = PUBLIC_CONTROL_PLANE_URL + serverInfo.branding.logo_url;
		}
		if (serverInfo.branding.favicon_url?.startsWith('/')) {
			serverInfo.branding.favicon_url = PUBLIC_CONTROL_PLANE_URL + serverInfo.branding.favicon_url;
		}
	}

	return {
		serverInfo
	};
};
