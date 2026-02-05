<script lang="ts">
	import MarkdownViewer from '$lib/components/MarkdownViewer.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import {
		Breadcrumb,
		BreadcrumbList,
		BreadcrumbItem,
		BreadcrumbLink,
		BreadcrumbSeparator,
		BreadcrumbPage,
		Separator
	} from '@evc/ui-svelte';
	import { page } from '$app/stores';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const shareUrl = $derived($page.url.href);
	const backUrl = $derived(`/${data.parentSlug}`);
</script>

<svelte:head>
	<title>{data.file.name} - {data.share.path} - Relay</title>
	<meta name="description" content="View document: {data.file.path}" />
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
				<MarkdownViewer content={data.content} />
			</div>
		</div>
	</article>
</div>
