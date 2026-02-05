<script lang="ts">
  import { cn } from '../../utils.js';
  import { setSidebarContext, type SidebarContextValue } from './context.svelte.js';
  import { useIsMobile } from './use-mobile.svelte.js';
  import { TooltipProvider } from '../tooltip/index.js';
  import type { HTMLAttributes } from 'svelte/elements';
  import type { Snippet } from 'svelte';

  const SIDEBAR_COOKIE_NAME = 'sidebar_state';
  const SIDEBAR_COOKIE_MAX_AGE = 60 * 60 * 24 * 7;
  const SIDEBAR_WIDTH = '16rem';
  const SIDEBAR_WIDTH_ICON = '3rem';
  const SIDEBAR_KEYBOARD_SHORTCUT = 'b';

  type Props = HTMLAttributes<HTMLDivElement> & {
    defaultOpen?: boolean;
    open?: boolean;
    onOpenChange?: (open: boolean) => void;
    class?: string;
    children?: Snippet;
  };

  let {
    defaultOpen = true,
    open: openProp,
    onOpenChange,
    class: className,
    style,
    children,
    ...restProps
  }: Props = $props();

  const mobile = useIsMobile();
  let openMobileState = $state(false);
  let internalOpenState = $state(defaultOpen);

  const isOpen = $derived(openProp ?? internalOpenState);
  const sidebarState = $derived<'expanded' | 'collapsed'>(isOpen ? 'expanded' : 'collapsed');

  function setOpen(value: boolean) {
    if (onOpenChange) {
      onOpenChange(value);
    } else {
      internalOpenState = value;
    }
    // Set cookie to persist state
    if (typeof document !== 'undefined') {
      document.cookie = `${SIDEBAR_COOKIE_NAME}=${value}; path=/; max-age=${SIDEBAR_COOKIE_MAX_AGE}`;
    }
  }

  function setOpenMobile(value: boolean) {
    openMobileState = value;
  }

  function toggleSidebar() {
    if (mobile.current) {
      openMobileState = !openMobileState;
    } else {
      setOpen(!isOpen);
    }
  }

  // Keyboard shortcut
  $effect(() => {
    if (typeof window === 'undefined') return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === SIDEBAR_KEYBOARD_SHORTCUT && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        toggleSidebar();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  });

  const contextValue: SidebarContextValue = {
    get state() { return sidebarState; },
    get open() { return isOpen; },
    setOpen,
    get openMobile() { return openMobileState; },
    setOpenMobile,
    get isMobile() { return mobile.current; },
    toggleSidebar,
  };

  setSidebarContext(contextValue);
</script>

<TooltipProvider delayDuration={0}>
  <div
    style="--sidebar-width: {SIDEBAR_WIDTH}; --sidebar-width-icon: {SIDEBAR_WIDTH_ICON}; {style || ''}"
    class={cn(
      'group/sidebar-wrapper flex min-h-svh w-full has-[[data-variant=inset]]:bg-sidebar',
      className
    )}
    {...restProps}
  >
    {@render children?.()}
  </div>
</TooltipProvider>
