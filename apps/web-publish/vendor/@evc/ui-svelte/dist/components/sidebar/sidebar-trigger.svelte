<script lang="ts">
  import { cn } from '../../utils.js';
  import { getSidebarContext } from './context.svelte.js';
  import { Button } from '../button/index.js';
  import type { HTMLButtonAttributes } from 'svelte/elements';
  import type { Snippet } from 'svelte';

  type Props = HTMLButtonAttributes & {
    class?: string;
    children?: Snippet;
  };

  let { class: className, onclick, children, ...restProps }: Props = $props();

  const ctx = getSidebarContext();

  function handleClick(event: MouseEvent) {
    if (onclick) {
      // @ts-ignore - Event types mismatch between Svelte and native
      onclick(event);
    }
    ctx.toggleSidebar();
  }
</script>

<Button
  data-sidebar="trigger"
  variant="ghost"
  size="icon"
  class={cn('h-7 w-7', className)}
  onclick={handleClick}
  {...restProps}
>
  {#if children}
    {@render children()}
  {:else}
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect width="18" height="18" x="3" y="3" rx="2"/>
      <path d="M9 3v18"/>
    </svg>
    <span class="sr-only">Toggle Sidebar</span>
  {/if}
</Button>
