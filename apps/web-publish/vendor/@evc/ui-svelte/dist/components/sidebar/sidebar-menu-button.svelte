<script lang="ts">
  import { cn } from '../../utils.js';
  import { getSidebarContext } from './context.svelte.js';
  import { sidebarMenuButtonVariants, type SidebarMenuButtonVariants } from './variants.js';
  import { Tooltip, TooltipTrigger, TooltipContent } from '../tooltip/index.js';
  import type { HTMLButtonAttributes } from 'svelte/elements';
  import type { Snippet } from 'svelte';

  type Props = HTMLButtonAttributes &
    SidebarMenuButtonVariants & {
      class?: string;
      isActive?: boolean;
      tooltip?: string;
      children?: Snippet;
    };

  let {
    class: className,
    variant = 'default',
    size = 'default',
    isActive = false,
    tooltip,
    children,
    ...restProps
  }: Props = $props();

  const ctx = getSidebarContext();
  const showTooltip = $derived(tooltip && ctx.state === 'collapsed' && !ctx.isMobile);
</script>

{#if showTooltip}
  <Tooltip>
    <TooltipTrigger>
      {#snippet child({ props })}
        <button
          {...props}
          data-sidebar="menu-button"
          data-size={size}
          data-active={isActive}
          class={cn(sidebarMenuButtonVariants({ variant, size }), className)}
          {...restProps}
        >
          {@render children?.()}
        </button>
      {/snippet}
    </TooltipTrigger>
    <TooltipContent side="right">
      {tooltip}
    </TooltipContent>
  </Tooltip>
{:else}
  <button
    data-sidebar="menu-button"
    data-size={size}
    data-active={isActive}
    class={cn(sidebarMenuButtonVariants({ variant, size }), className)}
    {...restProps}
  >
    {@render children?.()}
  </button>
{/if}
