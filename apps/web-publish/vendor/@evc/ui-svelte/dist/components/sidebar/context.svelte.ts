import { getContext, setContext } from 'svelte';

const SIDEBAR_CONTEXT_KEY = Symbol('sidebar');

export type SidebarState = 'expanded' | 'collapsed';

export interface SidebarContextValue {
  state: SidebarState;
  open: boolean;
  setOpen: (open: boolean) => void;
  openMobile: boolean;
  setOpenMobile: (open: boolean) => void;
  isMobile: boolean;
  toggleSidebar: () => void;
}

export function setSidebarContext(value: SidebarContextValue) {
  setContext(SIDEBAR_CONTEXT_KEY, value);
}

export function getSidebarContext(): SidebarContextValue {
  const context = getContext<SidebarContextValue>(SIDEBAR_CONTEXT_KEY);
  if (!context) {
    throw new Error('useSidebar must be used within a SidebarProvider.');
  }
  return context;
}
