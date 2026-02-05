const MOBILE_BREAKPOINT = 768;

export function useIsMobile(): { readonly current: boolean } {
  let isMobile = $state(false);

  $effect(() => {
    // Check if we're in a browser environment
    if (typeof window === 'undefined') return;

    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
    const onChange = () => {
      isMobile = window.innerWidth < MOBILE_BREAKPOINT;
    };

    mql.addEventListener('change', onChange);
    isMobile = window.innerWidth < MOBILE_BREAKPOINT;

    return () => mql.removeEventListener('change', onChange);
  });

  return {
    get current() {
      return isMobile;
    }
  };
}
