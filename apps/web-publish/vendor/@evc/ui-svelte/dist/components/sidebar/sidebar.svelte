<script lang="ts">
  import { cn } from '../../utils.js';
  import { getSidebarContext } from './context.svelte.js';
  import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '../sheet/index.js';
  import type { HTMLAttributes } from 'svelte/elements';
  import type { Snippet } from 'svelte';

  const SIDEBAR_WIDTH_MOBILE = '18rem';

  type Props = HTMLAttributes<HTMLDivElement> & {
    side?: 'left' | 'right';
    variant?: 'sidebar' | 'floating' | 'inset';
    collapsible?: 'offcanvas' | 'icon' | 'none';
    class?: string;
    children?: Snippet;
  };

  let {
    side = 'left',
    variant = 'sidebar',
    collapsible = 'offcanvas',
    class: className,
    children,
    ...restProps
  }: Props = $props();

  const ctx = getSidebarContext();
</script>

{#if collapsible === 'none'}
  <div
    class={cn(
      'flex h-full w-[--sidebar-width] flex-col bg-sidebar text-sidebar-foreground',
      className
    )}
    {...restProps}
  >
    {@render children?.()}
  </div>
{:else if ctx.isMobile}
  <Sheet open={ctx.openMobile} onOpenChange={ctx.setOpenMobile}>
    <SheetContent
      data-sidebar="sidebar"
      data-mobile="true"
      class="w-[--sidebar-width] bg-sidebar p-0 text-sidebar-foreground [&>button]:hidden"
      style="--sidebar-width: {SIDEBAR_WIDTH_MOBILE}"
      {side}
    >
      <SheetHeader class="sr-only">
        <SheetTitle>Sidebar</SheetTitle>
        <SheetDescription>Displays the mobile sidebar.</SheetDescription>
      </SheetHeader>
      <div class="flex h-full w-full flex-col">
        {@render children?.()}
      </div>
    </SheetContent>
  </Sheet>
{:else}
  <div
    class="group peer hidden text-sidebar-foreground md:block"
    data-state={ctx.state}
    data-collapsible={ctx.state === 'collapsed' ? collapsible : ''}
    data-variant={variant}
    data-side={side}
  >
    <!-- This is what handles the sidebar gap on desktop -->
    <div
      class={cn(
        'relative w-[--sidebar-width] bg-transparent transition-[width] duration-200 ease-linear',
        'group-data-[collapsible=offcanvas]:w-0',
        'group-data-[side=right]:rotate-180',
        variant === 'floating' || variant === 'inset'
          ? 'group-data-[collapsible=icon]:w-[calc(var(--sidebar-width-icon)_+_theme(spacing.4))]'
          : 'group-data-[collapsible=icon]:w-[--sidebar-width-icon]'
      )}
    ></div>
    <div
      class={cn(
        'fixed inset-y-0 z-10 hidden h-svh w-[--sidebar-width] transition-[left,right,width] duration-200 ease-linear md:flex',
        side === 'left'
          ? 'left-0 group-data-[collapsible=offcanvas]:left-[calc(var(--sidebar-width)*-1)]'
          : 'right-0 group-data-[collapsible=offcanvas]:right-[calc(var(--sidebar-width)*-1)]',
        variant === 'floating' || variant === 'inset'
          ? 'p-2 group-data-[collapsible=icon]:w-[calc(var(--sidebar-width-icon)_+_theme(spacing.4)_+2px)]'
          : 'group-data-[collapsible=icon]:w-[--sidebar-width-icon] group-data-[side=left]:border-r group-data-[side=right]:border-l',
        className
      )}
      {...restProps}
    >
      <div
        data-sidebar="sidebar"
        class="flex h-full w-full flex-col bg-sidebar group-data-[variant=floating]:rounded-lg group-data-[variant=floating]:border group-data-[variant=floating]:border-sidebar-border group-data-[variant=floating]:shadow"
      >
        {@render children?.()}
      </div>
    </div>
  </div>
{/if}
