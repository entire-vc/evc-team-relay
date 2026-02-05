<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAnchorAttributes } from 'svelte/elements';
  import type { Snippet } from 'svelte';

  type Props = HTMLAnchorAttributes & {
    class?: string;
    size?: 'sm' | 'md';
    isActive?: boolean;
    children?: Snippet;
  };

  let { class: className, size = 'md', isActive = false, children, ...restProps }: Props = $props();
</script>

<a
  data-sidebar="menu-sub-button"
  data-size={size}
  data-active={isActive}
  class={cn(
    'flex h-7 min-w-0 -translate-x-px items-center gap-2 overflow-hidden rounded-md px-2 text-sidebar-foreground outline-none ring-sidebar-ring hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 active:bg-sidebar-accent active:text-sidebar-accent-foreground disabled:pointer-events-none disabled:opacity-50 aria-disabled:pointer-events-none aria-disabled:opacity-50 [&>span:last-child]:truncate [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-sidebar-accent-foreground',
    'data-[active=true]:bg-sidebar-accent data-[active=true]:text-sidebar-accent-foreground',
    size === 'sm' && 'text-xs',
    size === 'md' && 'text-sm',
    'group-data-[collapsible=icon]:hidden',
    className
  )}
  {...restProps}
>
  {@render children?.()}
</a>
