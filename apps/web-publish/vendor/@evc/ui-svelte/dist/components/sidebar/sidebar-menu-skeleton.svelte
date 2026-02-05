<script lang="ts">
  import { cn } from '../../utils.js';
  import { Skeleton } from '../skeleton/index.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
    showIcon?: boolean;
  };

  let { class: className, showIcon = false, ...restProps }: Props = $props();

  // Random width between 50 to 90%
  const width = `${Math.floor(Math.random() * 40) + 50}%`;
</script>

<div
  data-sidebar="menu-skeleton"
  class={cn('flex h-8 items-center gap-2 rounded-md px-2', className)}
  {...restProps}
>
  {#if showIcon}
    <Skeleton class="size-4 rounded-md" data-sidebar="menu-skeleton-icon" />
  {/if}
  <Skeleton
    class="h-4 max-w-[--skeleton-width] flex-1"
    data-sidebar="menu-skeleton-text"
    style="--skeleton-width: {width}"
  />
</div>
