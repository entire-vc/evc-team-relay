<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';
  import type { Snippet } from 'svelte';

  type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
    children?: Snippet;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<div
  data-sidebar="group-label"
  class={cn(
    'flex h-8 shrink-0 items-center rounded-md px-2 text-xs font-medium text-sidebar-foreground/70 outline-none ring-sidebar-ring transition-[margin,opacity] duration-200 ease-linear focus-visible:ring-2 [&_svg]:size-4 [&_svg]:shrink-0',
    'group-data-[collapsible=icon]:-mt-8 group-data-[collapsible=icon]:opacity-0',
    className
  )}
  {...restProps}
>
  {@render children?.()}
</div>
