<script lang="ts">
	import MarkdownViewer from '$lib/components/MarkdownViewer.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import { extractDescription } from '$lib/markdown';
	import {
		Breadcrumb,
		BreadcrumbList,
		BreadcrumbItem,
		BreadcrumbLink,
		BreadcrumbSeparator,
		BreadcrumbPage,
		Separator
	} from '@entire-vc/ui-svelte';
	import { page } from '$app/stores';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const shareUrl = $derived($page.url.href);
	const backUrl = $derived(`/${data.parentSlug}`);
	const description = $derived(
		extractDescription(data.content || '', `View document: ${data.file.path}`)
	);
	const branding = $derived($page.data?.serverInfo?.branding);
	const pageTitle = $derived(data.file.name);
</script>

<svelte:head>
	<title>{pageTitle} - {data.share.path} - {branding?.name || 'Relay'}</title>
	<meta name="description" content={description} />
	<meta property="og:title" content="{pageTitle} - {data.share.path}" />
	<meta property="og:description" content={description} />
	<meta property="og:type" content="article" />
	<meta property="og:url" content={shareUrl} />
	{#if branding?.logo_url}
		<meta property="og:image" content={branding.logo_url} />
	{/if}
	<meta name="twitter:card" content="summary" />
	<meta name="twitter:title" content="{pageTitle} - {data.share.path}" />
	<meta name="twitter:description" content={description} />
	{#if branding?.logo_url}
		<meta name="twitter:image" content={branding.logo_url} />
	{/if}
	{#if data.share.web_noindex}
		<meta name="robots" content="noindex" />
	{/if}
</svelte:head>

<div class="w-full">
	<article class="max-w-[900px] mx-auto">
		<Breadcrumb class="mb-4 text-sm" style="padding-left: 0;">
			<BreadcrumbList class="gap-1.5 pl-0">
				<BreadcrumbItem>
					<BreadcrumbLink href="/{data.parentSlug}" class="text-muted-foreground hover:text-foreground transition-colors">
						{data.share.path}
					</BreadcrumbLink>
				</BreadcrumbItem>
				<BreadcrumbSeparator class="text-muted-foreground/50" />
				<BreadcrumbItem>
					<BreadcrumbPage class="text-muted-foreground font-normal">{data.file.name}</BreadcrumbPage>
				</BreadcrumbItem>
			</BreadcrumbList>
		</Breadcrumb>

		<StatusBar
			visibility={data.share.visibility}
			updatedAt={data.share.updated_at || data.share.created_at}
			{shareUrl}
			showBackButton={true}
			{backUrl}
		/>

		<h1 class="text-3xl font-bold text-foreground mb-4 leading-tight">{data.file.name}</h1>
		<Separator class="mb-8" />

		<div class="flex gap-8 items-start">
			<div class="flex-1 min-w-0 max-w-[800px]">
				<MarkdownViewer content={data.content} slug={data.share.web_slug} folderItems={data.folderItems} />
			</div>
		</div>
	</article>
</div>
