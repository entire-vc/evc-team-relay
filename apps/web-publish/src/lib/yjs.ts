/**
 * Yjs integration for real-time document sync.
 *
 * Connects to y-sweet relay server via WebSocket and provides
 * reactive document content.
 */

import * as Y from 'yjs';
import { WebsocketProvider } from 'y-websocket';
import { writable, type Readable } from 'svelte/store';
import type { RelayToken } from './api';

export interface YjsConnection {
	/** The Yjs document */
	doc: Y.Doc;
	/** WebSocket provider */
	provider: WebsocketProvider;
	/** Reactive store with document content */
	content: Readable<string>;
	/** Connection status */
	status: Readable<'connecting' | 'connected' | 'disconnected'>;
	/** Disconnect and cleanup */
	destroy: () => void;
}

/**
 * Connect to y-sweet relay for real-time document sync.
 *
 * @param token Relay token from control plane
 * @returns YjsConnection with reactive content store
 */
export function connectToRelay(token: RelayToken): YjsConnection {
	const doc = new Y.Doc();
	const content = writable<string>('');
	const status = writable<'connecting' | 'connected' | 'disconnected'>('connecting');

	// Get the text content from Yjs document
	// The document structure matches what the Obsidian plugin uses
	const text = doc.getText('content');

	// Update content store when text changes
	const updateContent = () => {
		content.set(text.toString());
	};

	// Subscribe to text changes
	text.observe(updateContent);

	// Build WebSocket URL with token
	const wsUrl = buildWebSocketUrl(token.relay_url, token.doc_id, token.token);

	// Create WebSocket provider
	// y-sweet uses the same wire protocol as y-websocket
	const provider = new WebsocketProvider(wsUrl, token.doc_id, doc, {
		connect: true,
		params: { token: token.token }
	});

	// Track connection status
	provider.on('status', (event: { status: string }) => {
		if (event.status === 'connected') {
			status.set('connected');
		} else if (event.status === 'disconnected') {
			status.set('disconnected');
		} else {
			status.set('connecting');
		}
	});

	// Handle sync event - content is ready
	provider.on('sync', (synced: boolean) => {
		if (synced) {
			updateContent();
		}
	});

	// Initial content update
	updateContent();

	return {
		doc,
		provider,
		content: { subscribe: content.subscribe },
		status: { subscribe: status.subscribe },
		destroy: () => {
			text.unobserve(updateContent);
			provider.destroy();
			doc.destroy();
		}
	};
}

/**
 * Build WebSocket URL for y-sweet connection.
 */
function buildWebSocketUrl(relayUrl: string, docId: string, token: string): string {
	// Normalize the relay URL
	let baseUrl = relayUrl;

	// Convert http(s) to ws(s)
	if (baseUrl.startsWith('https://')) {
		baseUrl = 'wss://' + baseUrl.slice(8);
	} else if (baseUrl.startsWith('http://')) {
		baseUrl = 'ws://' + baseUrl.slice(7);
	}

	// Remove trailing slash
	baseUrl = baseUrl.replace(/\/$/, '');

	// y-sweet WebSocket URL format
	return baseUrl;
}

/**
 * Check if real-time sync is available for a share.
 */
export function isRealtimeSyncAvailable(docId: string | null | undefined): boolean {
	return !!docId && docId.length > 0;
}
