<script lang="ts">
  import { cn } from '../../utils.js';
  import ScrollBar from './scroll-bar.svelte';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
    orientation?: 'vertical' | 'horizontal' | 'both';
  };

  let { class: className, orientation = 'vertical', children, ...restProps }: Props = $props();
</script>

<div class={cn('relative overflow-hidden', className)} {...restProps}>
  <div class="h-full w-full overflow-auto rounded-[inherit] scrollbar-hide">
    {@render children?.()}
  </div>
  {#if orientation === 'vertical' || orientation === 'both'}
    <ScrollBar orientation="vertical" />
  {/if}
  {#if orientation === 'horizontal' || orientation === 'both'}
    <ScrollBar orientation="horizontal" />
  {/if}
</div>
