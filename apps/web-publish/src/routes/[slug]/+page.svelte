<script lang="ts">
	import MarkdownViewer from '$lib/components/MarkdownViewer.svelte';
	import EditableMarkdownViewer from '$lib/components/EditableMarkdownViewer.svelte';
	import LiveMarkdownViewer from '$lib/components/LiveMarkdownViewer.svelte';
	import CopyButton from '$lib/components/CopyButton.svelte';
	import TableOfContents from '$lib/components/TableOfContents.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import { extractTitle, extractDescription, estimateReadingTime } from '$lib/markdown';
	import { isRealtimeSyncAvailable } from '$lib/yjs';
	import type { PageData } from './$types';
	import { page } from '$app/stores';
	import {
		Dialog,
		DialogContent,
		DialogHeader,
		DialogTitle,
		DialogDescription,
		DialogFooter,
		Input,
		Button,
		Label,
		Card,
		CardHeader,
		CardTitle,
		CardDescription,
		CardContent,
		Separator,
		Alert,
		AlertDescription
	} from '@entire-vc/ui-svelte';

	let { data }: { data: PageData } = $props();

	let password = $state('');
	let passwordError = $state('');
	let isAuthenticating = $state(false);
	let showTOC = $state(false);
	let dialogOpen = $state(true);

	// Derived reactive values
	const showPasswordModal = $derived(data.needsPassword);
	const title = $derived(
		data.isFolder ? data.share.path : extractTitle(data.content || '', data.share.path)
	);
	const description = $derived(
		data.isFolder
			? `Shared folder: ${data.share.path}`
			: extractDescription(data.content || '', `View shared document: ${data.share.path}`)
	);
	const readingTime = $derived(data.content ? estimateReadingTime(data.content) : 0);
	const shareUrl = $derived($page.url.href);
	const branding = $derived($page.data?.serverInfo?.branding);
	const formattedPath = $derived(data.share.path.split('/').pop()?.replace('.md', '') || title);
	const lastUpdated = $derived(
		new Date(data.share.updated_at || data.share.created_at).toLocaleDateString('en-US', {
			year: 'numeric',
			month: 'long',
			day: 'numeric'
		})
	);
	// Check if real-time sync is available for this share
	const hasRealtimeSync = $derived(isRealtimeSyncAvailable(data.share.web_doc_id));

	// Toggle TOC visibility
	function toggleTOC() {
		showTOC = !showTOC;
	}

	async function handlePasswordSubmit() {
		if (!password) {
			passwordError = 'Password is required';
			return;
		}

		isAuthenticating = true;
		passwordError = '';

		try {
			// Submit password to our API route
			const response = await fetch('/api/auth', {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json'
				},
				body: JSON.stringify({
					slug: data.share.web_slug,
					password
				})
			});

			if (!response.ok) {
				const errorData = await response
					.json()
					.catch(() => ({ message: 'Authentication failed' }));
				throw new Error(errorData.message || 'Authentication failed');
			}

			// Success! Reload page to fetch content with valid session
			window.location.reload();
		} catch (err) {
			passwordError = err instanceof Error ? err.message : 'Authentication failed';
		} finally {
			isAuthenticating = false;
		}
	}
</script>

<svelte:head>
	<title>{title} - {branding?.name || 'Relay'}</title>
	<meta name="description" content={description} />
	<meta property="og:title" content={title} />
	<meta property="og:description" content={description} />
	<meta property="og:type" content="article" />
	<meta property="og:url" content={shareUrl} />
	{#if branding?.logo_url}
		<meta property="og:image" content={branding.logo_url} />
	{/if}
	<meta name="twitter:card" content="summary" />
	<meta name="twitter:title" content={title} />
	<meta name="twitter:description" content={description} />
	{#if branding?.logo_url}
		<meta name="twitter:image" content={branding.logo_url} />
	{/if}
	{#if data.share.web_noindex}
		<meta name="robots" content="noindex" />
	{/if}
</svelte:head>

{#if showPasswordModal}
	<Dialog bind:open={dialogOpen}>
		<DialogContent class="sm:max-w-md">
			<DialogHeader>
				<DialogTitle>Protected Document</DialogTitle>
				<DialogDescription>
					This document is password protected. Please enter the password to continue.
				</DialogDescription>
			</DialogHeader>

			<form
				onsubmit={(e) => {
					e.preventDefault();
					handlePasswordSubmit();
				}}
				class="space-y-4"
			>
				<div class="space-y-2">
					<Label for="password">Password</Label>
					<Input
						id="password"
						type="password"
						bind:value={password}
						placeholder="Enter password"
						disabled={isAuthenticating}
					/>
				</div>

				{#if passwordError}
					<Alert variant="destructive">
						<AlertDescription>{passwordError}</AlertDescription>
					</Alert>
				{/if}

				<DialogFooter>
					<Button type="submit" disabled={isAuthenticating} class="w-full">
						{isAuthenticating ? 'Authenticating...' : 'Submit'}
					</Button>
				</DialogFooter>
			</form>
		</DialogContent>
	</Dialog>
{:else if data.isFolder}
	<!-- Folder view -->
	<div class="w-full">
		<article class="max-w-[1200px] mx-auto">
			<StatusBar
				visibility={data.share.visibility}
				updatedAt={data.share.updated_at || data.share.created_at}
				{shareUrl}
			/>

			<h1 class="text-4xl font-bold text-foreground mb-4 leading-tight">{data.share.path}</h1>
			<Separator class="mb-8" />

			<div class="flex gap-8 items-start">
				<div class="flex-1 min-w-0 max-w-[800px]">
					{#if data.readmeContent}
						<!-- Show README.md content -->
						<MarkdownViewer content={data.readmeContent} slug={data.share.web_slug} folderItems={data.folderItems} />
					{:else if data.folderItems.length === 0}
						<Card class="text-center border-dashed">
							<CardHeader class="pb-2">
								<div class="text-6xl mb-4">📂</div>
								<CardTitle>Empty Folder</CardTitle>
								<CardDescription>This folder doesn't contain any items yet.</CardDescription>
							</CardHeader>
						</Card>
					{:else}
						<Card
							class="text-center bg-gradient-to-br from-muted to-secondary border-0 shadow-md"
						>
							<CardHeader class="pb-2">
								<div class="text-6xl mb-4">📁</div>
								<CardTitle>{data.share.path}</CardTitle>
								<CardDescription>
									This folder contains {data.folderItems.length} item{data.folderItems.length !== 1
										? 's'
										: ''}.
								</CardDescription>
							</CardHeader>
							<CardContent>
								<p class="text-primary font-medium">Select a document from the sidebar to view.</p>
							</CardContent>
						</Card>
					{/if}
				</div>
			</div>
		</article>
	</div>
{:else}
	<!-- Document view -->
	<div class="w-full">
		<article class="max-w-[1200px] mx-auto">
			<StatusBar
				visibility={data.share.visibility}
				updatedAt={data.share.updated_at || data.share.created_at}
				{shareUrl}
			/>

			<h1 class="text-4xl font-bold text-foreground mb-4 leading-tight">{title}</h1>
			<Separator class="mb-8" />

			<div class="flex gap-8 items-start">
				<div class="flex-1 min-w-0 max-w-[800px]">
					{#if hasRealtimeSync}
						<LiveMarkdownViewer
							content={data.content || ''}
							docId={data.share.web_doc_id}
							slug={data.share.web_slug}
							sessionToken={data.sessionToken}
							folderItems={data.folderItems}
						/>
					{:else if data.canEdit}
						<EditableMarkdownViewer
							content={data.content || ''}
							slug={data.share.web_slug}
							canEdit={data.canEdit}
							sessionToken={data.sessionToken}
							authToken={data.authToken}
							folderItems={data.folderItems}
						/>
					{:else}
						<MarkdownViewer content={data.content || ''} slug={data.share.web_slug} folderItems={data.folderItems} />
					{/if}
				</div>
			</div>
		</article>
	</div>
{/if}
